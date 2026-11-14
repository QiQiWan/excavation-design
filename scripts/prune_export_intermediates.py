"""清理导出目录里的构建中间件，只保留最终 zip 与共享缓存目录。

用法：
    python scripts/prune_export_intermediates.py            # 只统计，不删
    python scripts/prune_export_intermediates.py --apply    # 真删

只匹配本项目导出的构建目录（project-* 且含 coordinated_delivery / formal_issue /
cad_package 标记），不碰 detail-sheets、report-charts 等共享缓存，也不碰任何 .zip。
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPORT_DIR = ROOT / "services" / "api" / "exports"
MARKERS = ("_coordinated_delivery_", "_formal_issue_", "_cad_package_")


def _targets() -> list[Path]:
    if not EXPORT_DIR.exists():
        return []
    rows = []
    for item in EXPORT_DIR.iterdir():
        if not item.is_dir() or not item.name.startswith("project-"):
            continue
        if any(marker in item.name for marker in MARKERS):
            rows.append(item)
    return sorted(rows)


def _size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="真正删除；默认只统计")
    args = parser.parse_args()

    rows = _targets()
    total = sum(_size(row) for row in rows)
    print(f"匹配构建目录 {len(rows)} 个，合计 {total / 1048576:.1f} MB")
    for row in rows:
        print(f"  {_size(row) / 1048576:8.1f} MB  {row.name}")

    if not args.apply:
        print("\n未删除（dry-run）。确认后加 --apply。")
        return 0

    removed = 0
    freed = 0
    for row in rows:
        size = _size(row)
        try:
            shutil.rmtree(row)
        except OSError as exc:
            print(f"  跳过 {row.name}: {exc}")
            continue
        removed += 1
        freed += size
    print(f"\n已删除 {removed} 个目录，释放 {freed / 1048576:.1f} MB（zip 全部保留）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
