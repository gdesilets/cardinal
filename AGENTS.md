# Cardinal / Rejection Block Super Skill

This project's real deliverable is the `rejection-block-analyst` skill: an ICT
(Inner Circle Trader) trading analyst grounded in a bundled, curated Discord
research corpus (SQLite, no live market data).

## Skill location (read this before searching elsewhere)

The canonical, edited copy lives at `.claude/skills/rejection-block-analyst/`. A
byte-identical mirror lives at `.codex/skills/rejection-block-analyst/` so Codex
CLI's project-skill discovery (`.codex/skills/`) finds it too -- both tools read
the same `SKILL.md` format, they just look in different default directories.

**Always edit the `.claude/skills/` copy. Never edit the `.codex/skills/` copy
directly** -- after any change, re-sync it:

```bash
python scripts/sync_codex_skill.py
```

## Everything else in this repo

`pipeline/` is the raw Discord scrape + database-build machinery that produced
the skill's bundled SQLite database. It is **not tracked in git** and **not read
by the skill at runtime** -- see `pipeline/README.md` if you need to rebuild or
extend the corpus (e.g. adding evidence for the `ifvg-retrace` strategy that's
currently seeded as a `planned` placeholder).

## Adding a new strategy family

See `.claude/skills/rejection-block-analyst/references/adding_a_strategy.md`.
