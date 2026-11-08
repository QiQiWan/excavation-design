"""实际工程案例导入（Python 版，UTF-8 安全）。

替代 packages/sample-data/actual-project/import_actual_project.ps1。
原 PowerShell 脚本在中文 Windows 下用 Invoke-RestMethod 读取 UTF-8 JSON 时
按本地代码页解码，中文字段被截断导致 ConvertFrom-Json 失败，导入在第 2 步
之后就中断。本脚本统一按 UTF-8 收发。

用法：
    python scripts/import_actual_project.py                  # 新建项目并跑完全部 7 步
    python scripts/import_actual_project.py --project <id>    # 从第 3 步继续已有项目
    python scripts/import_actual_project.py --api http://127.0.0.1:8002
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "packages/sample-data/actual-project"
CSV = DATA / "actual_project_boreholes_24x6layers.csv"
EXCAVATION = DATA / "actual_project_excavation_payload.json"
API = "http://127.0.0.1:8002"


def call(method: str, path: str, body: dict | None = None,
         raw: bytes | None = None, content_type: str = "application/json"):
    if raw is None and body is not None:
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(API + path, data=raw, method=method)
    if raw is not None:
        req.add_header("Content-Type", content_type)
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            payload = resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise SystemExit(f"[FAIL] {method} {path} -> {exc.code}\n{detail[:1200]}") from exc
    if not payload:
        return None
    return json.loads(payload.decode("utf-8"))


def upload_csv(project_id: str) -> dict:
    """multipart/form-data 上传钻孔 CSV，手工拼包避免额外依赖。"""
    boundary = "----pitguardimport7f3a"
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{CSV.name}"\r\n'
        f"Content-Type: text/csv\r\n\r\n"
    ).encode("utf-8")
    body = head + CSV.read_bytes() + f"\r\n--{boundary}--\r\n".encode("utf-8")
    return call("POST", f"/api/projects/{project_id}/boreholes/import-csv",
                raw=body, content_type=f"multipart/form-data; boundary={boundary}")


def main() -> int:
    global API
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default=API)
    parser.add_argument("--project", default=None, help="已有项目 ID，从第 3 步继续")
    args = parser.parse_args()

    API = args.api.rstrip("/")

    if args.project:
        project_id = args.project
        print(f"[skip] 复用项目 {project_id}（钻孔已导入）")
    else:
        print("[1/7] 创建项目")
        project = call("POST", "/api/projects", {
            "name": "实际工程案例 · 24 钻孔 6 层",
            "location": "本地工程坐标",
            "designSettings": {
                "autoCenterExcavationOnGeology": False,
                "groundwaterLevel": -20.0,
                "surcharge": 20.0,
                "minimumSegmentLength": 0.5,
                "supportLevelDepthsM": [0.0, 4.0, 7.2, 10.3, 13.3],
            },
        })
        project_id = project["id"]
        print(f"      项目 ID: {project_id}")

        print("[2/7] 导入钻孔 CSV")
        imported = upload_csv(project_id)
        if not imported.get("success"):
            raise SystemExit(f"钻孔导入失败: {imported.get('errors')}")
        print(f"      钻孔 {imported['boreholeCount']} 个，分层 {imported['layerCount']} 条，"
              f"地层 {imported['stratumCount']} 种")
        for warning in imported.get("warnings") or []:
            print(f"      [警告] {warning[:120]}")

    print("[3/7] 生成地质模型")
    call("POST", f"/api/projects/{project_id}/geology/build-model?grid_size=10")
    geo = call("GET", f"/api/projects/{project_id}/geology/model")
    strata = geo.get("strata") or geo.get("model", {}).get("strata") or []
    print(f"      地层 {len(strata)} 种")

    print("[4/7] 创建基坑轮廓")
    payload = json.loads(EXCAVATION.read_text(encoding="utf-8"))
    call("POST", f"/api/projects/{project_id}/excavation", payload)
    print("      轮廓已导入")

    print("[5/7] 自动生成地连墙")
    call("POST", f"/api/projects/{project_id}/design/auto-diaphragm-wall")

    print("[6/7] 生成支撑（异步任务，由 pitguard-worker 执行）")
    task = call("POST", f"/api/projects/{project_id}/tasks", {
        "operation": "support_layout_optimization",
        "payload": {"preset": "balanced", "maxCandidates": 3},
    })
    task_id = task["id"]
    print(f"      任务 {task_id[:12]}… 已提交，等待 worker")
    deadline = time.time() + 900
    last = ""
    while time.time() < deadline:
        current = call("GET", f"/api/tasks/{task_id}")
        status = current.get("status")
        step = current.get("currentStep") or ""
        if (status, step) != last:
            print(f"      [{status}] {step}")
            last = (status, step)
        if status in ("success", "failed", "cancelled"):
            break
        time.sleep(2)
    else:
        raise SystemExit("等待支撑生成任务超时（15 分钟）")
    if status != "success":
        raise SystemExit(f"支撑生成失败：{status}\n{json.dumps(current, ensure_ascii=False)[:1200]}")
    print("      支撑体系已生成")

    print("[7/7] 对齐墙底标高 -32.8 m")
    retaining = call("GET", f"/api/projects/{project_id}/design/retaining-system")
    walls = retaining.get("diaphragmWalls") or []
    for wall in walls:
        wall["bottomElevation"] = -32.8
        wall["bottomElevationSource"] = "imported"
        wall["bottomElevationLocked"] = True
        wall["sourceBottomElevation"] = -32.8
    call("PUT", f"/api/projects/{project_id}/design/retaining-system", retaining)
    print(f"      地连墙 {len(walls)} 段，墙底 -32.8 m")

    print()
    print(f"完成。打开：http://127.0.0.1:5173/projects/{project_id}")
    print("支撑标高：0.0 / 4.0 / 7.2 / 10.3 / 13.3 m")
    print("提醒：圆砾层弹性模量与渗透系数需按勘察报告复核，计算前还应核对支撑平面布置。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
