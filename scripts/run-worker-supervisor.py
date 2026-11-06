from __future__ import annotations

"""Restartable one-task-per-process worker supervisor.

The HTTP API never executes engineering calculations.  Each worker daemon claims
one queued task, executes it in a fresh OS process, then exits.  This supervisor
restarts the daemon so NumPy/Matplotlib/native allocator state cannot accumulate
across tasks and an OOM/resource abort cannot take down the API process.
"""

import atexit
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "services" / "api"
RUNTIME_DIR = ROOT / "runtime"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))
from app.services.runtime_diagnostics import append_event, memory_event
from app.services.runtime_resource_policy import adaptive_resource_policy, mb
PID_FILE = Path(os.getenv("PITGUARD_WORKER_SUPERVISOR_PID", RUNTIME_DIR / "worker-supervisor.pid"))
STOP = False
CHILD: subprocess.Popen[str] | None = None
CHILD_JOB_HANDLE: int | None = None



def _close_windows_job() -> None:
    global CHILD_JOB_HANDLE
    if os.name != "nt" or not CHILD_JOB_HANDLE:
        CHILD_JOB_HANDLE = None
        return
    try:
        import ctypes
        ctypes.windll.kernel32.CloseHandle(CHILD_JOB_HANDLE)
    except Exception:
        pass
    CHILD_JOB_HANDLE = None


def _assign_windows_memory_job(process: subprocess.Popen[str]) -> None:
    """Apply an OS-enforced private-memory ceiling to the calculation child.

    The in-process watchdog remains the primary source of a readable task error,
    while the Windows Job Object is the final guard against a sudden allocation
    spike occurring between watchdog samples. Failure to install the Job Object
    is logged and the worker continues under the Python watchdog.
    """
    global CHILD_JOB_HANDLE
    if os.name != "nt":
        return
    enabled = str(os.getenv("PITGUARD_WORKER_OS_MEMORY_LIMIT", "1")).strip().lower()
    if enabled in {"0", "false", "no", "off"}:
        return
    try:
        import ctypes
        from ctypes import wintypes

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL

        policy = adaptive_resource_policy(role="worker")
        policy_cap_mb = max(2048, int(mb(policy.get("workerHardLimitBytes"))))
        explicit = str(os.getenv("PITGUARD_WORKER_OS_HARD_LIMIT_MB", "")).strip()
        cap_mb = max(512, int(float(explicit))) if explicit else policy_cap_mb
        cap_bytes = int(cap_mb * 1048576)

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            raise ctypes.WinError(ctypes.get_last_error())
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_PROCESS_MEMORY | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        info.ProcessMemoryLimit = cap_bytes
        JobObjectExtendedLimitInformation = 9
        if not kernel32.SetInformationJobObject(job, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)):
            error = ctypes.get_last_error()
            kernel32.CloseHandle(job)
            raise ctypes.WinError(error)
        process_handle = wintypes.HANDLE(int(getattr(process, "_handle")))
        if not kernel32.AssignProcessToJobObject(job, process_handle):
            error = ctypes.get_last_error()
            kernel32.CloseHandle(job)
            raise ctypes.WinError(error)
        CHILD_JOB_HANDLE = int(getattr(job, "value", job))
        append_event(
            "worker-supervisor",
            "windows-job-memory-limit-installed",
            childPid=process.pid,
            hardLimitMb=cap_mb,
        )
    except Exception as exc:
        append_event(
            "worker-supervisor",
            "windows-job-memory-limit-failed",
            childPid=process.pid,
            error=str(exc),
        )


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _acquire_lock() -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    if PID_FILE.exists():
        try:
            previous = int(PID_FILE.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            previous = 0
        if _pid_alive(previous):
            raise SystemExit(f"PitGuard worker supervisor is already running (pid={previous}).")
        try:
            PID_FILE.unlink()
        except OSError:
            pass
    descriptor = os.open(PID_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(str(os.getpid()))


def _cleanup() -> None:
    global CHILD
    if CHILD is not None and CHILD.poll() is None:
        try:
            CHILD.terminate()
            CHILD.wait(timeout=8)
        except Exception:
            try:
                CHILD.kill()
            except Exception:
                pass
    _close_windows_job()
    try:
        if PID_FILE.exists() and PID_FILE.read_text(encoding="utf-8").strip() == str(os.getpid()):
            PID_FILE.unlink()
    except OSError:
        pass


def _handle_signal(_signum: int, _frame: object) -> None:
    global STOP
    STOP = True
    if CHILD is not None and CHILD.poll() is None:
        try:
            CHILD.terminate()
        except OSError:
            pass


def main() -> int:
    global CHILD
    _acquire_lock()
    atexit.register(_cleanup)
    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        signum = getattr(signal, name, None)
        if signum is not None:
            signal.signal(signum, _handle_signal)

    python = os.getenv("PYTHON_BIN") or sys.executable
    env = os.environ.copy()
    env["PITGUARD_TASK_EXECUTION_MODE"] = "worker"
    env["PITGUARD_PROCESS_ROLE"] = "worker"
    env.setdefault("PITGUARD_WORKER_EXIT_AFTER_TASK", "true")
    env.setdefault("PITGUARD_WORKER_POLL_SECONDS", "0.8")
    env["PYTHONPATH"] = str(API_DIR) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")

    crashes: list[float] = []
    print(f"[PitGuard] worker supervisor pid={os.getpid()} python={python}", flush=True)
    memory_event("worker-supervisor", "supervisor-start", python=python)
    while not STOP:
        started = time.monotonic()
        CHILD = subprocess.Popen(
            [python, "-m", "app.tasks.worker_daemon"],
            cwd=API_DIR,
            env=env,
            text=True,
        )
        child_pid = CHILD.pid
        append_event("worker-supervisor", "worker-start", childPid=child_pid)
        _assign_windows_memory_job(CHILD)
        return_code = CHILD.wait()
        append_event(
            "worker-supervisor",
            "worker-exit",
            childPid=child_pid,
            returnCode=return_code,
            elapsedSeconds=round(time.monotonic() - started, 3),
        )
        _close_windows_job()
        CHILD = None
        if STOP:
            break
        elapsed = time.monotonic() - started
        now = time.monotonic()
        crashes = [stamp for stamp in crashes if now - stamp < 60.0]
        if return_code != 0:
            crashes.append(now)
            print(
                f"[PitGuard] worker exited code={return_code} after {elapsed:.1f}s; "
                "API remains online and the supervisor will start a clean worker.",
                flush=True,
            )
        delay = min(10.0, 0.5 + max(0, len(crashes) - 2) * 1.5)
        time.sleep(delay)
    memory_event("worker-supervisor", "supervisor-stop")
    print("[PitGuard] worker supervisor stopped.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
