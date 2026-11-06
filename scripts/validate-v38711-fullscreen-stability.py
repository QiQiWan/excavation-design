from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "apps" / "web" / "src"
VIEWERS = [
    SRC / "viewers" / "Engineering3DViewer.tsx",
    SRC / "viewers" / "ProjectSceneViewer.tsx",
    SRC / "viewers" / "WallCloud3DViewer.tsx",
    SRC / "viewers" / "RebarIfcViewer.tsx",
]

failures: list[str] = []
for path in VIEWERS:
    text = path.read_text(encoding="utf-8")
    if "createStableWebGLRenderer" not in text:
        failures.append(f"{path.name}: shared renderer runtime missing")
    if "releaseStableWebGLRenderer" not in text:
        failures.append(f"{path.name}: explicit GPU cleanup missing")
    if "startStableRenderLoop" not in text:
        failures.append(f"{path.name}: stable render loop missing")
    if "<FullscreenShell" not in text:
        failures.append(f"{path.name}: fullscreen wrapper missing")

client = (SRC / "api" / "client.ts").read_text(encoding="utf-8")
if "method === 'GET'" not in client or "responseCache.clear()" not in client:
    failures.append("API client: request isolation/cache invalidation guard missing")
workspace = (SRC / "pages" / "CoreProjectWorkspace.tsx").read_text(encoding="utf-8")
if "refreshGenerationRef" not in workspace:
    failures.append("Workspace: stale refresh generation guard missing")
app = (SRC / "app" / "App.tsx").read_text(encoding="utf-8")
if "systemReadiness" not in app or "runtimeHealthBadge" not in app:
    failures.append("App: runtime readiness monitor missing")

if failures:
    raise SystemExit("\n".join(failures))
print(f"V3.87.11 validation passed: {len(VIEWERS)} WebGL viewers have fullscreen and stability guards.")
