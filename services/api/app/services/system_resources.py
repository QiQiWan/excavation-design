from __future__ import annotations

"""Small cross-platform process/system resource helpers.

The previous implementation read only Linux ``/proc`` files.  On Windows that
silently returned zero RSS and invented an 8 GB / 4 GB memory snapshot, so the
calculation worker could pass every memory gate and then be terminated by the
operating system.  These helpers use native Win32 APIs when ``/proc`` is not
available and keep a conservative POSIX fallback for other platforms.
"""

import ctypes
import os
import sys
from typing import Any

try:
    import resource
except ImportError:  # pragma: no cover - Windows
    resource = None  # type: ignore[assignment]


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


class _ProcessMemoryCountersEx(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


def _windows_memory_status() -> tuple[int, int] | None:
    if os.name != "nt":
        return None
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        global_memory_status = kernel32.GlobalMemoryStatusEx
        global_memory_status.argtypes = [ctypes.POINTER(_MemoryStatusEx)]
        global_memory_status.restype = ctypes.c_int
        status = _MemoryStatusEx()
        status.dwLength = ctypes.sizeof(status)
        if not global_memory_status(ctypes.byref(status)):
            return None
        return int(status.ullTotalPhys), int(status.ullAvailPhys)
    except (AttributeError, OSError, ValueError):
        return None


def _windows_process_counters() -> dict[str, Any] | None:
    if os.name != "nt":
        return None
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        get_current_process = kernel32.GetCurrentProcess
        # Win32 HANDLE is pointer-sized.  Leaving the ctypes default ``c_int``
        # truncates it on 64-bit Python and made every process counter return 0.
        get_current_process.argtypes = []
        get_current_process.restype = ctypes.c_void_p
        process = get_current_process()
        if not process:
            return None
        counters = _ProcessMemoryCountersEx()
        counters.cb = ctypes.sizeof(counters)
        get_memory_info = getattr(kernel32, "K32GetProcessMemoryInfo", None)
        source = "kernel32.K32GetProcessMemoryInfo"
        if get_memory_info is None:
            psapi = ctypes.WinDLL("psapi", use_last_error=True)
            get_memory_info = psapi.GetProcessMemoryInfo
            source = "psapi.GetProcessMemoryInfo"
        get_memory_info.argtypes = [ctypes.c_void_p, ctypes.POINTER(_ProcessMemoryCountersEx), ctypes.c_ulong]
        get_memory_info.restype = ctypes.c_int
        ok = get_memory_info(process, ctypes.byref(counters), counters.cb)
        if not ok:
            return None
        result: dict[str, Any] = {
            "rssBytes": int(counters.WorkingSetSize),
            "peakRssBytes": int(counters.PeakWorkingSetSize),
            "privateBytes": int(counters.PrivateUsage),
            "pagefileBytes": int(counters.PagefileUsage),
            "peakPagefileBytes": int(counters.PeakPagefileUsage),
            "pageFaultCount": int(counters.PageFaultCount),
            "metricsSource": source,
        }
        if result["rssBytes"] <= 0 and result["privateBytes"] <= 0:
            return None
        return result
    except (AttributeError, OSError, ValueError):
        try:
            import psutil  # type: ignore

            info = psutil.Process(os.getpid()).memory_info()
            rss = int(getattr(info, "rss", 0) or 0)
            private = int(getattr(info, "private", 0) or getattr(info, "pagefile", 0) or rss)
            if rss <= 0 and private <= 0:
                return None
            return {
                "rssBytes": rss,
                "peakRssBytes": rss,
                "privateBytes": private,
                "pagefileBytes": int(getattr(info, "pagefile", 0) or 0),
                "peakPagefileBytes": int(getattr(info, "pagefile", 0) or 0),
                "pageFaultCount": 0,
                "metricsSource": "psutil",
            }
        except (ImportError, OSError, ValueError, AttributeError):
            return None


def _proc_meminfo() -> dict[str, int]:
    values: dict[str, int] = {}
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as stream:
            for line in stream:
                key, _, rest = line.partition(":")
                if key not in {"MemTotal", "MemAvailable", "MemFree", "Buffers", "Cached"}:
                    continue
                fields = rest.strip().split()
                if fields:
                    values[key] = int(fields[0]) * 1024
    except OSError:
        return values
    if "MemAvailable" not in values:
        values["MemAvailable"] = values.get("MemFree", 0) + values.get("Buffers", 0) + values.get("Cached", 0)
    return values


def _sysconf_memory() -> tuple[int, int] | None:
    if os.name == "nt":
        return None
    try:
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        total_pages = int(os.sysconf("SC_PHYS_PAGES"))
        available_pages = int(os.sysconf("SC_AVPHYS_PAGES"))
        if page_size > 0 and total_pages > 0:
            return page_size * total_pages, max(0, page_size * available_pages)
    except (AttributeError, OSError, ValueError):
        return None
    return None


def process_rss_bytes() -> int:
    """Return current resident memory, not a startup estimate."""
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as stream:
            for line in stream:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        pass
    windows = _windows_process_counters()
    if windows is not None:
        return int(windows.get("rssBytes") or 0)
    if resource is not None:
        try:
            value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
            # macOS reports bytes; Linux/BSD commonly report KiB.
            return value if sys.platform == "darwin" else value * 1024
        except (OSError, ValueError):
            pass
    return 0



def process_memory_counters() -> dict[str, Any]:
    """Return current/peak RSS and committed-private memory when available."""
    windows = _windows_process_counters()
    if windows is not None:
        return windows
    rss = process_rss_bytes()
    peak_rss = rss
    virtual = 0
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as stream:
            for line in stream:
                if line.startswith("VmHWM:"):
                    peak_rss = int(line.split()[1]) * 1024
                elif line.startswith("VmSize:"):
                    virtual = int(line.split()[1]) * 1024
    except OSError:
        pass
    return {
        "rssBytes": int(rss),
        "peakRssBytes": int(peak_rss),
        "privateBytes": int(rss),
        "pagefileBytes": int(virtual),
        "peakPagefileBytes": 0,
        "pageFaultCount": 0,
        "metricsSource": "proc_or_resource",
    }


def process_effective_memory_bytes() -> int:
    counters = process_memory_counters()
    return max(int(counters.get("rssBytes") or 0), int(counters.get("privateBytes") or 0))

def physical_memory_bytes() -> tuple[int, int]:
    """Return ``(total, available)`` physical-memory bytes."""
    proc = _proc_meminfo()
    if proc.get("MemTotal"):
        return int(proc["MemTotal"]), int(proc.get("MemAvailable") or 0)
    windows = _windows_memory_status()
    if windows is not None:
        return windows
    sysconf = _sysconf_memory()
    if sysconf is not None:
        return sysconf
    return 0, 0


def memory_debug_snapshot() -> dict[str, Any]:
    total, available = physical_memory_bytes()
    counters = process_memory_counters()
    return {
        "platform": sys.platform,
        "totalBytes": int(total),
        "availableBytes": int(available),
        "processRssBytes": int(counters.get("rssBytes") or 0),
        "processPeakRssBytes": int(counters.get("peakRssBytes") or 0),
        "processPrivateBytes": int(counters.get("privateBytes") or 0),
        "processPagefileBytes": int(counters.get("pagefileBytes") or 0),
        "processPeakPagefileBytes": int(counters.get("peakPagefileBytes") or 0),
        "pageFaultCount": int(counters.get("pageFaultCount") or 0),
        "processEffectiveBytes": int(max(counters.get("rssBytes") or 0, counters.get("privateBytes") or 0)),
        "processMetricsAvailable": bool((counters.get("rssBytes") or 0) or (counters.get("privateBytes") or 0)),
        "processMetricsSource": str(counters.get("metricsSource") or "unavailable"),
        "nativeWindowsMetrics": os.name == "nt" and str(counters.get("metricsSource") or "").startswith(("kernel32", "psapi", "psutil")),
    }
