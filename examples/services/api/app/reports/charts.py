from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def _mpl():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
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
            ax.plot(xs, ys, linewidth=1.8, label="Excavation outline")
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
        ax.set_title("Support layout quality plan")
        ax.grid(True, alpha=0.25)
        path = _save(fig, out_dir / "support_layout_quality_plan.png")
        charts.append({"title": "支撑布置评分平面图", "path": str(path)})

    # V2.0.8 candidate scheme score and plan comparison for human-in-the-loop design selection.
    repair = getattr(latest, "support_layout_repair", None)
    candidates = list(getattr(repair, "candidates", []) or [])[:5] if repair else []
    if candidates:
        labels = [f"方案 {c.rank or idx + 1}" for idx, c in enumerate(candidates)]
        scores = [float(getattr(c, "score", 0.0) or 0.0) for c in candidates]
        fig, ax = plt.subplots(figsize=(6.4, 3.4))
        ax.bar(labels, scores)
        ax.set_ylim(0, 100)
        ax.set_ylabel("Score")
        ax.set_title("Support optimization candidate scores")
        ax.grid(True, axis="y", alpha=0.3)
        path = _save(fig, out_dir / "support_candidate_scores.png")
        charts.append({"title": "支撑优化候选方案评分图", "path": str(path)})

        fig, axes = plt.subplots(1, len(candidates), figsize=(max(7.0, 2.4 * len(candidates)), 3.0), squeeze=False)
        for ax, candidate in zip(axes[0], candidates):
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
            ax.set_title(f"{candidate.rank or '-'} / {getattr(candidate, 'score', 0):.1f}")
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
            ax.set_xlabel("Total lateral pressure (kPa)")
            ax.set_ylabel("Elevation (m)")
            ax.set_title("Wall pressure profile")
            ax.grid(True, alpha=0.3)
            path = _save(fig, out_dir / "wall_pressure_profile.png")
            charts.append({"title": "墙体土压力图", "path": str(path)})
        for title, fname, values, xlabel in [
            ("Wall displacement", "wall_displacement.png", [p.displacement or 0 for p in pts], "Displacement (mm)"),
            ("Wall moment", "wall_moment.png", [p.moment for p in pts], "Moment (kN·m/m)"),
            ("Wall shear", "wall_shear.png", [p.shear for p in pts], "Shear (kN/m)"),
        ]:
            fig, ax = plt.subplots(figsize=(4.8, 3.6))
            ax.plot(values, elev, marker="o", linewidth=1.8)
            ax.set_xlabel(xlabel)
            ax.set_ylabel("Elevation (m)")
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
            ax.set_xlabel("Chainage (m)")
            ax.set_ylabel("Moment (kN·m)")
            ax.set_title("Wale beam moment envelope")
            ax.legend()
            ax.grid(True, alpha=0.3)
            path = _save(fig, out_dir / "wale_moment_envelope.png")
            charts.append({"title": "围檩弯矩包络图", "path": str(path)})
            fig, ax = plt.subplots(figsize=(5.5, 3.4))
            ax.plot(x, [p.get("maxAbsShear", 0) for p in pts], label="|V|", linewidth=1.8)
            ax.set_xlabel("Chainage (m)")
            ax.set_ylabel("Shear (kN)")
            ax.set_title("Wale beam shear envelope")
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
        labels = [i[0][-6:] for i in items]
        values = [i[1] for i in items]
        ax.bar(labels, values)
        ax.set_ylabel("Design axial force (kN)")
        ax.set_title("Top support axial forces")
        ax.tick_params(axis="x", rotation=60)
        ax.grid(True, axis="y", alpha=0.3)
        path = _save(fig, out_dir / "support_axial_forces.png")
        charts.append({"title": "支撑轴力柱状图", "path": str(path)})

    checks = latest.checks or []
    if checks:
        counts = Counter(str(c.get("status", "unknown")) for c in checks)
        fig, ax = plt.subplots(figsize=(4.8, 3.4))
        labels = list(counts.keys())
        values = [counts[k] for k in labels]
        ax.bar(labels, values)
        ax.set_ylabel("Count")
        ax.set_title("Check result summary")
        ax.grid(True, axis="y", alpha=0.3)
        path = _save(fig, out_dir / "check_summary.png")
        charts.append({"title": "校核结果汇总图", "path": str(path)})
    return charts
