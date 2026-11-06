from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def _mpl():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    # Register a system CJK font explicitly.  Matplotlib's isolated cache may
    # not discover TTC collections even when fontconfig can, which previously
    # produced blank Chinese labels in exported engineering charts.
    cjk_paths = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
    ]
    cjk_name = None
    for raw in cjk_paths:
        font_path = Path(raw)
        if not font_path.exists():
            continue
        try:
            font_manager.fontManager.addfont(str(font_path))
            cjk_name = font_manager.FontProperties(fname=str(font_path)).get_name()
            break
        except Exception:
            continue
    plt.rcParams["font.sans-serif"] = [
        *( [cjk_name] if cjk_name else [] ),
        "Microsoft YaHei", "SimHei", "Arial Unicode MS", "DejaVu Sans",
    ]
    plt.rcParams["axes.unicode_minus"] = False
    return plt


def _latest(project: Any):
    return project.calculation_results[-1] if getattr(project, "calculation_results", None) else None


def _save(fig, path: Path) -> Path:
    fig.tight_layout()
    fig.savefig(path, dpi=180, bbox_inches="tight")
    import matplotlib.pyplot as plt
    plt.close(fig)
    return path


def generate_report_charts(project: Any, output_dir: str | Path) -> list[dict[str, str]]:
    """Generate PNG charts for the DOCX report.

    The charts are intentionally generated from already exported calculation
    data, so the report remains consistent with JSON/IFC/model results.
    """
    latest = _latest(project)
    out_dir = Path(output_dir) / "report-charts"
    out_dir.mkdir(parents=True, exist_ok=True)
    charts: list[dict[str, str]] = []
    if not latest:
        return charts
    plt = _mpl()

    # V2.0.5 support layout quality plan for the report cover page.
    ret = getattr(project, "retaining_system", None)
    if ret and getattr(ret, "supports", None):
        fig, ax = plt.subplots(figsize=(6.4, 4.6))
        excavation = getattr(project, "excavation", None)
        if excavation and getattr(excavation, "outline", None) and excavation.outline.points:
            pts = excavation.outline.points
            xs = [p.x for p in pts] + [pts[0].x]
            ys = [p.y for p in pts] + [pts[0].y]
            ax.plot(xs, ys, linewidth=1.8, label="基坑轮廓")
            for obs in getattr(excavation, "obstacles", []) or []:
                if not getattr(obs, "active", True):
                    continue
                opt = getattr(obs, "outline", None)
                if opt and len(opt.points) >= 3:
                    ox = [p.x for p in opt.points] + [opt.points[0].x]
                    oy = [p.y for p in opt.points] + [opt.points[0].y]
                    ax.fill(ox, oy, alpha=0.16)
        highlight_ids = set()
        highlight_fail_ids = set()
        q = getattr(latest, "support_layout_quality", None)
        if q:
            for h in getattr(q, "highlights", []) or []:
                oid = h.get("objectId") if isinstance(h, dict) else None
                if oid:
                    highlight_ids.add(oid)
                    if h.get("severity") == "fail":
                        highlight_fail_ids.add(oid)
        for support in ret.supports:
            width = 1.4 if support.id not in highlight_ids else 2.8
            # Use line styles rather than relying on a single color legend; the PNG is for quick review.
            linestyle = "-" if support.id not in highlight_ids else "--"
            ax.plot([support.start.x, support.end.x], [support.start.y, support.end.y], linewidth=width, linestyle=linestyle)
            mx = (support.start.x + support.end.x) / 2.0
            my = (support.start.y + support.end.y) / 2.0
            if support.id in highlight_ids:
                ax.text(mx, my, support.code, fontsize=6)
        for col in getattr(ret, "columns", []) or []:
            ax.scatter([col.location.x], [col.location.y], s=18, marker="s")
        ax.set_aspect("equal", adjustable="box")
        ax.set_xlabel("X (m)")
        ax.set_ylabel("Y (m)")
        ax.set_title("当前采用方案支撑平面")
        ax.grid(True, alpha=0.25)
        path = _save(fig, out_dir / "support_layout_quality_plan.png")
        charts.append({"title": "支撑布置评分平面图", "path": str(path)})

    # V2.0.8 candidate scheme score and plan comparison for human-in-the-loop design selection.
    project_repair = getattr(getattr(project, "retaining_system", None), "support_layout_repair", None)
    repair = project_repair or getattr(latest, "support_layout_repair", None)
    candidates = list(getattr(repair, "candidates", []) or [])[:3] if repair else []
    if candidates:
        labels = [f"方案 {chr(65 + idx)}" for idx, _ in enumerate(candidates)]
        scores = [float(getattr(c, "score", 0.0) or 0.0) for c in candidates]
        if any(score > 0.0 for score in scores):
            fig, ax = plt.subplots(figsize=(6.4, 3.4))
            ax.bar(labels, scores)
            upper = max(100.0, max(scores) * 1.15)
            ax.set_ylim(0, upper)
            ax.set_ylabel("拓扑预检评分")
            ax.set_title("支撑候选方案拓扑预检评分")
            ax.grid(True, axis="y", alpha=0.3)
            path = _save(fig, out_dir / "support_candidate_scores.png")
            charts.append({"title": "支撑优化候选方案评分图", "path": str(path)})

        fig, axes = plt.subplots(1, len(candidates), figsize=(max(7.0, 2.4 * len(candidates)), 3.0), squeeze=False)
        for index, (ax, candidate) in enumerate(zip(axes[0], candidates)):
            geom = getattr(candidate, "plan_geometry", {}) or {}
            outline = geom.get("outline", [])
            if outline:
                xs = [p.get("x", 0) for p in outline] + [outline[0].get("x", 0)]
                ys = [p.get("y", 0) for p in outline] + [outline[0].get("y", 0)]
                ax.plot(xs, ys, linewidth=1.2)
            for obs in geom.get("obstacles", []) or []:
                pts = obs.get("points", [])
                if len(pts) >= 3:
                    ox = [p.get("x", 0) for p in pts] + [pts[0].get("x", 0)]
                    oy = [p.get("y", 0) for p in pts] + [pts[0].get("y", 0)]
                    ax.fill(ox, oy, alpha=0.16)
            for support in geom.get("supports", []) or []:
                start, end = support.get("start", {}), support.get("end", {})
                width = 2.2 if support.get("changed") else 1.0
                linestyle = "--" if support.get("changed") else "-"
                ax.plot([start.get("x", 0), end.get("x", 0)], [start.get("y", 0), end.get("y", 0)], linewidth=width, linestyle=linestyle)
            for col in geom.get("columns", []) or []:
                loc = col.get("location", {})
                ax.scatter([loc.get("x", 0)], [loc.get("y", 0)], s=10, marker="s")
            ax.set_aspect("equal", adjustable="box")
            score = float(getattr(candidate, "score", 0.0) or 0.0)
            title = f"方案 {chr(65 + index)}"
            if score > 0.0:
                title += f" · {score:.1f}"
            ax.set_title(title)
            ax.set_xticks([])
            ax.set_yticks([])
        path = _save(fig, out_dir / "support_candidate_plan_comparison.png")
        charts.append({"title": "支撑优化候选方案平面比选图", "path": str(path)})

    # Select the stage with the largest wall moment as a representative wall diagram.
    wall_results = [sr for sr in latest.stage_results if sr.wall_internal_force and sr.wall_internal_force.points]
    if wall_results:
        sr = max(wall_results, key=lambda x: abs(x.wall_internal_force.max_moment or 0.0))
        pts = sr.wall_internal_force.points
        depth = [p.depth for p in pts]
        elev = [p.elevation for p in pts]
        pressure_points = sorted(sr.pressure_profile.points, key=lambda p: p.depth)
        if pressure_points:
            fig, ax = plt.subplots(figsize=(4.8, 3.6))
            ax.plot([p.total_pressure for p in pressure_points], [p.elevation for p in pressure_points], marker="o", linewidth=1.8)
            ax.set_xlabel("总侧向压力（kPa）")
            ax.set_ylabel("标高（m）")
            ax.set_title("墙体侧向压力分布")
            ax.grid(True, alpha=0.3)
            path = _save(fig, out_dir / "wall_pressure_profile.png")
            charts.append({"title": "墙体土压力图", "path": str(path)})
        for title, fname, values, xlabel in [
            ("墙体位移", "wall_displacement.png", [p.displacement or 0 for p in pts], "位移（mm）"),
            ("墙体弯矩", "wall_moment.png", [p.moment for p in pts], "弯矩（kN·m/m）"),
            ("墙体剪力", "wall_shear.png", [p.shear for p in pts], "剪力（kN/m）"),
        ]:
            fig, ax = plt.subplots(figsize=(4.8, 3.6))
            ax.plot(values, elev, marker="o", linewidth=1.8)
            ax.set_xlabel(xlabel)
            ax.set_ylabel("标高（m）")
            ax.set_title(title)
            ax.grid(True, alpha=0.3)
            path = _save(fig, out_dir / fname)
            charts.append({"title": title, "path": str(path)})

    envelopes = (latest.report_diagram_data or {}).get("waleEnvelopes", [])
    if envelopes:
        env = envelopes[0]
        pts = env.get("points", [])
        if pts:
            x = [p.get("chainage", 0) for p in pts]
            fig, ax = plt.subplots(figsize=(5.5, 3.4))
            ax.plot(x, [p.get("maxPositiveMoment", 0) for p in pts], label="M+", linewidth=1.8)
            ax.plot(x, [p.get("maxNegativeMoment", 0) for p in pts], label="M-", linewidth=1.8)
            ax.set_xlabel("里程（m）")
            ax.set_ylabel("弯矩（kN·m）")
            ax.set_title("围檩弯矩包络")
            ax.legend()
            ax.grid(True, alpha=0.3)
            path = _save(fig, out_dir / "wale_moment_envelope.png")
            charts.append({"title": "围檩弯矩包络图", "path": str(path)})
            fig, ax = plt.subplots(figsize=(5.5, 3.4))
            ax.plot(x, [p.get("maxAbsShear", 0) for p in pts], label="|V|", linewidth=1.8)
            ax.set_xlabel("里程（m）")
            ax.set_ylabel("剪力（kN）")
            ax.set_title("围檩剪力包络")
            ax.grid(True, alpha=0.3)
            path = _save(fig, out_dir / "wale_shear_envelope.png")
            charts.append({"title": "围檩剪力包络图", "path": str(path)})

    # Support axial force bar chart by support code, using the max design force.
    support_forces = defaultdict(float)
    for sr in latest.stage_results:
        for f in sr.support_forces:
            if f.support_id:
                support_forces[f.support_id] = max(support_forces[f.support_id], float(f.axial_force_design or f.axial_force or 0.0))
    if support_forces:
        items = sorted(support_forces.items(), key=lambda kv: kv[1], reverse=True)[:20]
        fig, ax = plt.subplots(figsize=(7.0, 3.8))
        support_code_by_id = {
            str(getattr(support, "id", "")): str(getattr(support, "code", "") or getattr(support, "id", ""))
            for support in (getattr(ret, "supports", []) or [])
        }
        labels = [support_code_by_id.get(str(item_id), str(item_id)[-6:]) for item_id, _ in items]
        values = [value for _, value in items]
        ax.bar(labels, values)
        ax.set_ylabel("设计轴力（kN）")
        ax.set_title("控制支撑设计轴力")
        ax.tick_params(axis="x", rotation=60)
        ax.grid(True, axis="y", alpha=0.3)
        path = _save(fig, out_dir / "support_axial_forces.png")
        charts.append({"title": "支撑轴力柱状图", "path": str(path)})


    # Stability safety-factor distribution for project managers and reviewers.
    try:
        from app.services.core_engineering_presentation import build_stability_distribution
        distribution = build_stability_distribution(project)
        factors = [item for item in (distribution.get("factors") or []) if item.get("marginRatio") is not None]
        if factors:
            fig, ax = plt.subplots(figsize=(6.4, max(2.8, 0.42 * len(factors) + 1.2)))
            labels = [str(item.get("label") or item.get("code") or "稳定检查") for item in factors]
            margins = [float(item.get("marginRatio") or 0.0) for item in factors]
            y = list(range(len(labels)))
            ax.barh(y, margins)
            ax.axvline(1.0, linewidth=1.4, linestyle="--", label="规范限值")
            ax.set_yticks(y)
            try:
                from matplotlib import font_manager
                cjk_path = next((Path(raw) for raw in (
                    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                    "C:/Windows/Fonts/msyh.ttc",
                    "C:/Windows/Fonts/simhei.ttf",
                ) if Path(raw).exists()), None)
                if cjk_path:
                    ax.set_yticklabels(labels, fontproperties=font_manager.FontProperties(fname=str(cjk_path)))
                else:
                    ax.set_yticklabels(labels)
            except Exception:
                ax.set_yticklabels(labels)
            ax.set_xlabel("计算安全系数 / 规范限值")
            ax.set_title("稳定性安全系数裕度分布")
            ax.grid(True, axis="x", alpha=0.3)
            ax.legend()
            path = _save(fig, out_dir / "stability_factor_distribution.png")
            charts.append({"title": "稳定性安全系数分布图", "path": str(path)})
    except Exception:
        pass

    checks = latest.checks or []
    if checks:
        counts = Counter(str(c.get("status", "unknown")) for c in checks)
        fig, ax = plt.subplots(figsize=(4.8, 3.4))
        status_names = {"pass": "通过", "warning": "预警", "fail": "不通过", "manual_review": "人工复核"}
        keys = list(counts.keys())
        labels = [status_names.get(key, key) for key in keys]
        values = [counts[key] for key in keys]
        ax.bar(labels, values)
        ax.set_ylabel("数量")
        ax.set_title("校核结果汇总")
        ax.grid(True, axis="y", alpha=0.3)
        path = _save(fig, out_dir / "check_summary.png")
        charts.append({"title": "校核结果汇总图", "path": str(path)})
    return charts
