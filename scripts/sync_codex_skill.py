#!/usr/bin/env python3
"""Mirror .claude/skills/<name>/ into .codex/skills/<name>/ for every skill folder.

Claude Code discovers project skills at .claude/skills/<name>/SKILL.md; Codex CLI
discovers the same SKILL.md format at .codex/skills/<name>/SKILL.md (native support
added Dec 2025). Both tools need their own copy on disk -- Windows symlinks aren't
reliably usable without admin/Developer Mode, so this does a real file copy instead.

.claude/skills/ is the canonical, edited copy. Re-run this after editing any skill.

Usage:
  python scripts/sync_codex_skill.py           # sync all skills
  python scripts/sync_codex_skill.py <name>     # sync just one
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_ROOT = REPO_ROOT / ".claude" / "skills"
DEST_ROOT = REPO_ROOT / ".codex" / "skills"


def sync_skill(name: str) -> None:
    """Merge-copy source onto dest (overwriting changed files, adding new ones,
    removing files/dirs no longer in source) without deleting dest's own
    directories first. OneDrive can hold a lock on a just-emptied directory long
    enough to make an rmtree-then-copytree approach fail with WinError 5, so this
    updates in place instead."""
    source = SOURCE_ROOT / name
    dest = DEST_ROOT / name
    if not source.is_dir():
        raise SystemExit(f"No skill at {source}")
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, dest, dirs_exist_ok=True)

    source_rel = {p.relative_to(source) for p in source.rglob("*")}
    dest_rel = sorted((p.relative_to(dest) for p in dest.rglob("*")), reverse=True)
    for rel in dest_rel:
        if rel in source_rel:
            continue
        stale = dest / rel
        if stale.is_dir():
            stale.rmdir()
        else:
            stale.unlink()
    print(f"Synced {source} -> {dest}")


def main() -> None:
    names = sys.argv[1:]
    if not names:
        if not SOURCE_ROOT.is_dir():
            raise SystemExit(f"No skills found at {SOURCE_ROOT}")
        names = sorted(p.name for p in SOURCE_ROOT.iterdir() if p.is_dir())
    DEST_ROOT.mkdir(parents=True, exist_ok=True)
    for name in names:
        sync_skill(name)


if __name__ == "__main__":
    main()
