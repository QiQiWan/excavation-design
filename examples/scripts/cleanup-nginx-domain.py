#!/usr/bin/env python3
from __future__ import annotations
import argparse
import re
import shutil
from datetime import datetime
from pathlib import Path


def strip_comments(line: str) -> str:
    out = []
    quote = None
    escaped = False
    for char in line:
        if escaped:
            out.append(char); escaped = False; continue
        if char == "\\":
            out.append(char); escaped = True; continue
        if quote:
            out.append(char)
            if char == quote: quote = None
            continue
        if char in {"\"", "'"}:
            quote = char; out.append(char); continue
        if char == "#":
            out.extend(" " if tail != "\n" else "\n" for tail in line[len(out):])
            break
        out.append(char)
    if len(out) < len(line):
        out.extend(" " if tail != "\n" else "\n" for tail in line[len(out):])
    return "".join(out)


def server_blocks(text: str):
    clean_lines = [strip_comments(line) for line in text.splitlines(keepends=True)]
    clean = "".join(clean_lines)
    token = re.compile(r"\bserver\s*\{")
    for match in token.finditer(clean):
        depth = 0; quote = None; escaped = False
        for pos in range(match.start(), len(clean)):
            char = clean[pos]
            if escaped: escaped = False; continue
            if char == "\\": escaped = True; continue
            if quote:
                if char == quote: quote = None
                continue
            if char in {"\"", "'"}: quote = char; continue
            if char == "{": depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    yield match.start(), pos + 1, clean[match.start():pos + 1]
                    break


def remove_domain_blocks(path: Path, domain: str, backup_dir: Path) -> int:
    try: text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError): return 0
    blocks = list(server_blocks(text))
    removals = []
    pattern = re.compile(r"\bserver_name\s+([^;]*\b" + re.escape(domain) + r"\b[^;]*);", re.I)
    for start, end, block in blocks:
        if pattern.search(block): removals.append((start, end))
    if not removals: return 0
    resolved = path.resolve()
    backup_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(resolved, backup_dir / (resolved.name + ".bak"))
    for start, end in reversed(removals):
        text = text[:start] + "\n# PitGuard removed a legacy/conflicting server block for " + domain + "\n" + text[end:]
    resolved.write_text(text, encoding="utf-8")
    return len(removals)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--domain", required=True)
    parser.add_argument("--exclude", required=True)
    args = parser.parse_args()
    exclude = Path(args.exclude).resolve()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = Path("/etc/nginx/pitguard-disabled") / stamp
    candidates = []
    for base in (Path("/etc/nginx/conf.d"), Path("/etc/nginx/sites-enabled")):
        if base.exists(): candidates.extend(item for item in base.iterdir() if item.is_file() or item.is_symlink())
    removed = 0
    seen = set()
    for path in candidates:
        try: resolved = path.resolve()
        except OSError: continue
        if resolved == exclude or resolved in seen: continue
        seen.add(resolved)
        removed += remove_domain_blocks(path, args.domain, backup_dir)
    print(f"[PitGuard] Removed {removed} legacy/conflicting Nginx server block(s) for {args.domain}.")
    if removed: print(f"[PitGuard] Nginx backups: {backup_dir}")

if __name__ == "__main__": main()
