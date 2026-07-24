from pathlib import Path


def test_v3871_frontend_source_contract() -> None:
    root = Path(__file__).resolve().parents[3]
    main = (root / "apps/web/src/main.tsx").read_text(encoding="utf-8")
    result = (root / "apps/web/src/viewers/ResultViewer.tsx").read_text(encoding="utf-8")
    scheme = (root / "apps/web/src/components/SchemeComparisonPanel.tsx").read_text(encoding="utf-8")
    core = (root / "apps/web/src/components/CoreEngineeringVisuals.tsx").read_text(encoding="utf-8")
    compact = (root / "apps/web/src/pages/CoreProjectWorkspace.tsx").read_text(encoding="utf-8")
    retaining = (root / "apps/web/src/viewers/RetainingSystemViewer.tsx").read_text(encoding="utf-8")
    active_styles = (root / "apps/web/src/app/styles.css").read_text(encoding="utf-8")
    deprecated_styles = (root / "apps/web/src/styles.css").read_text(encoding="utf-8")
    assert "import './styles.css';" not in main
    assert "import './app/styles.css';" in main
    assert ".designCorePanel{" in active_styles
    assert ".designCoreStages{" in active_styles
    assert "Deprecated V3.87 compatibility stylesheet" in deprecated_styles
    assert result.count("function statusText(") == 1
    assert "function optimizationStatusText(" in result
    for source in (result, scheme, core, compact, retaining):
        assert "transferBeams" in source
        assert "polyline" in source
