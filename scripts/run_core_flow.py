"""按 6 个核心阶段顺序跑完一个项目，并打印每步进度与结果。

用法：
    python scripts/run_core_flow.py --project <项目ID>
    python scripts/run_core_flow.py --project <项目ID> --from calculation   # 从某阶段起

设计基准的取值来自示例假定，不是工程结论。真实项目必须由设计人按勘察报告、
地方标准和企业规定填写，并在确认前独立复核。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

API = "http://127.0.0.1:8002"
ACTIVE = {"queued", "running", "pending"}

STAGES = ["basis", "scheme", "calculation", "reinforcement", "deliverables"]


def call(method: str, path: str, body: dict | None = None):
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(API + path, data=raw, method=method)
    if raw is not None:
        req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=1800) as resp:
            payload = resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise SystemExit(f"\n[FAIL] {method} {path} -> {exc.code}\n{detail[:1500]}") from exc
    return json.loads(payload.decode("utf-8")) if payload else None


def run_task(project_id: str, operation: str, payload: dict, label: str,
             timeout_s: int = 3600) -> dict:
    task = call("POST", f"/api/projects/{project_id}/tasks",
                {"operation": operation, "payload": payload})
    task_id = task["id"]
    print(f"    任务 {task_id[:14]}…  操作 {operation}")
    deadline = time.time() + timeout_s
    shown: dict[str, float] = {}
    while time.time() < deadline:
        current = call("GET", f"/api/tasks/{task_id}")
        status = current.get("status")
        step = current.get("currentStep") or ""
        progress = current.get("progress")
        key = f"{status}|{step}"
        now = time.time()
        if key not in shown or now - shown[key] > 25:
            shown[key] = now
            bar = f"{progress:>3}%" if isinstance(progress, int) else "  -"
            print(f"      {bar}  [{status}] {step}")
        if status in ("success", "failed", "cancelled"):
            break
        time.sleep(1.5)
    else:
        raise SystemExit(f"{label} 超时（{timeout_s}s）")
    if status != "success":
        err = current.get("error") or "(无错误说明)"
        logs = "\n".join((current.get("logs") or [])[-6:])
        raise SystemExit(f"\n[FAIL] {label}：{status}\n{err}\n--- 末尾日志 ---\n{logs}")
    return current


def stage_status(project_id: str) -> list[dict]:
    data = call("GET", f"/api/projects/{project_id}/design/core-status?detail=summary")
    return data["stages"]


def show_stages(project_id: str) -> None:
    mark = {"done": "✓", "active": "·", "pending": " ", "blocked": "✕"}
    for stage in stage_status(project_id):
        icon = mark.get(stage["status"], "?")
        print(f"    {icon} {stage['title']:6} {stage['status']:8} {stage['message']}")


def main() -> int:
    global API
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--api", default=API)
    parser.add_argument("--from", dest="start", default=None, choices=STAGES,
                        help="从这个阶段开始（前面的阶段保持不变）")
    args = parser.parse_args()
    API = args.api.rstrip("/")
    pid = args.project

    start_index = STAGES.index(args.start) if args.start else 0

    print("=" * 74)
    print("项目:", pid)
    print("=" * 74)
    print("\n[当前状态]")
    show_stages(pid)

    # ---------------------------------------------------------------- 1 设计基准
    if start_index <= 0:
        print("\n[1/5] 设计基准 —— 确认工程等级、安全等级、规范体系与荷载组合")
        project = call("GET", f"/api/projects/{pid}")
        settings = dict(project["designSettings"])
        settings.update({
            "designBasisConfirmed": True,
            "projectGrade": "一级",
            "excavationSafetyLevel": "一级",
            "safetyGrade": "一级",
            "siteComplexity": "复杂",
            "surroundingEnvironmentLevel": "较高",
            "standardProfile": "national_core",
            "loadCombinationPolicy": "standard",
            "groundwaterLevel": -20.0,
            "surcharge": 20.0,
        })
        updated = call("PUT", f"/api/projects/{pid}", {"designSettings": settings})
        s = updated["designSettings"]
        print(f"      已确认：等级 {s['projectGrade']} / 安全等级 "
              f"{s['excavationSafetyLevel']} / 规范 {s['standardProfile']} / "
              f"组合 {s['loadCombinationPolicy']}")
        print(f"      γG={s['loadGammaG']} γQ={s['loadGammaQ']} ψ={s['loadPsi']} "
              f"重要性系数={s['importanceFactor']}")

    # ---------------------------------------------------------------- 2 核心设计
    # 方案候选 → 计算 → 应用配筋 必须在同一条链里完成。
    # 拆成三个任务会出现死锁：配筋要求计算无硬失败，而部分硬失败正是
    # "输入配筋不足"，只能由配筋本身消除。
    if start_index <= 1:
        print("\n[2/5] 核心设计 —— 生成方案候选、完整计算、应用配筋（独立 worker 执行）")
        run_task(pid, "core_design",
                 {"preset": "balanced", "maxCandidates": 3, "rebarMode": "balanced"},
                 "核心设计")

    # ---------------------------------------------------------------- 3 成果交付
    if start_index <= 4:
        print("\n[3/5] 成果交付 —— 生成图纸、计算书与模型")
        run_task(pid, "full_delivery", {"topN": 0}, "成果交付")

    # ---------------------------------------------------------------- 汇总
    print("\n" + "=" * 74)
    print("[最终状态]")
    show_stages(pid)

    status = call("GET", f"/api/projects/{pid}/design/core-status?detail=summary")
    blockers = status.get("blockers") or []
    print(f"\n阻断项 {len(blockers)} 条")
    for item in blockers[:8]:
        print(f"    - {item}")

    print(f"\n打开：http://127.0.0.1:5173/projects/{pid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
