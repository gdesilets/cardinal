#!/usr/bin/env python3
"""
Query helper for the Powell Discord ICT/Rejection-Block research corpus
(discord_trading_research_3month.sqlite).

This exists so every invocation of the rejection-block-analyst skill
doesn't have to hand-roll SQL for the same handful of question shapes.
Everything it prints is corpus evidence, not live market data.

Usage:
  python query_corpus.py search "10am rb close"
  python query_corpus.py search "liquidity sweep" --limit 15
  python query_corpus.py findings                     # all RB findings
  python query_corpus.py findings --facet invalidation
  python query_corpus.py qa "nested rb"
  python query_corpus.py strategy 10am-key-open-rb     # strategy card by strategy_key
  python query_corpus.py strategies                    # list all strategies
  python query_corpus.py strategies --family ifvg --status planned
  python query_corpus.py strategy-report                # wins/losses/win_rate by strategy
  python query_corpus.py strategy-report --min-n 15
  python query_corpus.py wins --strategy 10am-key-open-rb --limit 10
  python query_corpus.py losses --instrument NQ --limit 10
  python query_corpus.py confluence-stats               # win/loss table by confluence
  python query_corpus.py confluence-stats --min-n 15
  python query_corpus.py trades --confluence rejection_block --outcome win --limit 10
  python query_corpus.py message 1527316703686692944    # full message + permalink
  python query_corpus.py sql "SELECT ... FROM ... LIMIT 20"   # raw SELECT escape hatch

Add --db <path> to point at a different sqlite file. By default this script
looks for the copy bundled alongside it at ../data/discord_trading_research_3month.sqlite
(so the whole skill folder is self-contained and portable to other projects),
then falls back to searching upward from the current working directory for a
sibling pipeline/discord_trading_research_3month.sqlite.
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

DB_FILENAME = "discord_trading_research_3month.sqlite"
DB_RELATIVE = Path("pipeline") / DB_FILENAME
BUNDLED_DB = Path(__file__).resolve().parent.parent / "data" / DB_FILENAME


def find_db() -> Path:
    if BUNDLED_DB.exists():
        return BUNDLED_DB
    starts = [Path.cwd(), Path(__file__).resolve().parent]
    for start in starts:
        cur = start
        for _ in range(8):
            candidate = cur / DB_RELATIVE
            if candidate.exists():
                return candidate
            if cur.parent == cur:
                break
            cur = cur.parent
    raise FileNotFoundError(
        f"Could not locate {DB_FILENAME}. Expected it bundled at "
        f"{BUNDLED_DB}, or in a pipeline/ folder above the "
        "current directory. Pass --db <path> explicitly."
    )


def connect(db_path: str | None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else find_db()
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    return con


def dump(rows) -> None:
    out = [dict(r) for r in rows]
    print(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"\n[{len(out)} row(s)]", file=sys.stderr)


def cmd_search(con, args):
    sql = """
    SELECT m.message_id, m.author_display_name, m.displayed_time,
           m.channel_name, m.permalink, m.permalink_confidence, m.relevance,
           snippet(messages_fts, 4, '>>', '<<', '...', 12) AS snippet
    FROM messages_fts
    JOIN messages m ON m.rowid = messages_fts.rowid
    WHERE messages_fts MATCH ?
    ORDER BY m.created_at_utc
    LIMIT ?
    """
    try:
        rows = con.execute(sql, (args.query, args.limit)).fetchall()
    except sqlite3.OperationalError as e:
        print(f"FTS query failed ({e}); falling back to LIKE search.", file=sys.stderr)
        like = f"%{args.query}%"
        rows = con.execute(
            """SELECT message_id, author_display_name, displayed_time, channel_name,
                      permalink, permalink_confidence, relevance,
                      substr(content_text, 1, 300) AS snippet
               FROM messages WHERE content_text LIKE ? LIMIT ?""",
            (like, args.limit),
        ).fetchall()
    dump(rows)


def cmd_findings(con, args):
    sql = "SELECT * FROM rejection_block_findings"
    params = ()
    if args.facet:
        sql += " WHERE facet = ?"
        params = (args.facet,)
    sql += " ORDER BY facet, finding_id"
    findings = con.execute(sql, params).fetchall()
    out = []
    for f in findings:
        d = dict(f)
        ev = con.execute(
            "SELECT message_id, evidence_role, excerpt FROM rejection_block_finding_evidence WHERE finding_id = ?",
            (f["finding_id"],),
        ).fetchall()
        d["evidence"] = [dict(e) for e in ev]
        out.append(d)
    print(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"\n[{len(out)} finding(s)]", file=sys.stderr)


def cmd_qa(con, args):
    like = f"%{args.query}%"
    rows = con.execute(
        """SELECT qa_id, normalized_question, answer_summary, status, topic, confidence,
                  question_message_id, answer_message_id
           FROM qa_pairs
           WHERE normalized_question LIKE ? OR answer_summary LIKE ?
           ORDER BY qa_id LIMIT ?""",
        (like, like, args.limit),
    ).fetchall()
    dump(rows)


def cmd_strategy(con, args):
    row = con.execute("SELECT * FROM v_strategy_cards WHERE strategy_key = ?", (args.strategy_key,)).fetchone()
    if not row:
        print(f"No strategy with strategy_key={args.strategy_key!r}. Run `strategies` to list valid keys.", file=sys.stderr)
        return
    d = dict(row)
    ev = con.execute(
        "SELECT message_id, evidence_role, excerpt FROM strategy_evidence WHERE strategy_id = ?",
        (d["strategy_id"],),
    ).fetchall()
    d["evidence"] = [dict(e) for e in ev]
    report = con.execute(
        "SELECT wins, losses, breakevens, decided_n, win_rate, sample_flag, explicit_links, inferred_links "
        "FROM v_strategy_report WHERE strategy_id = ?",
        (d["strategy_id"],),
    ).fetchone()
    if report:
        d["outcomes"] = dict(report)
    print(json.dumps(d, indent=2, ensure_ascii=False))


def cmd_strategies(con, args):
    sql = (
        "SELECT strategy_key, family, status, name, instrument_scope, timeframe_scope, session_scope "
        "FROM v_strategy_cards"
    )
    where, params = [], []
    if args.family:
        where.append("family = ?")
        params.append(args.family)
    if args.status:
        where.append("status = ?")
        params.append(args.status)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY family, strategy_key"
    rows = con.execute(sql, params).fetchall()
    dump(rows)


def cmd_strategy_report(con, args):
    rows = con.execute(
        """SELECT strategy_key, name, family, status, wins, losses, breakevens,
                  other_outcomes, decided_n, win_rate, sample_flag,
                  explicit_links, inferred_links
           FROM v_strategy_report
           WHERE decided_n >= ?
           ORDER BY (win_rate IS NULL), win_rate DESC""",
        (args.min_n,),
    ).fetchall()
    dump(rows)


def cmd_confluence_stats(con, args):
    rows = con.execute(
        """SELECT canonical_name, win_trades, loss_trades, breakeven_trades,
                  total_resolved_mentions, unresolved_or_other_mentions
           FROM v_win_loss_confluence_comparison
           WHERE total_resolved_mentions >= ?
           ORDER BY total_resolved_mentions DESC""",
        (args.min_n,),
    ).fetchall()
    dump(rows)


def cmd_trades(con, args):
    sql = """
    SELECT DISTINCT t.trade_id, t.trader, t.trade_date, t.instrument, t.direction,
           t.setup_name, t.timeframe, t.session_name, t.outcome, t.outcome_basis
    FROM trades t
    """
    joins = []
    where = []
    params: list = []
    if args.confluence:
        joins.append("JOIN trade_confluences tc ON tc.trade_id = t.trade_id JOIN confluences c ON c.confluence_id = tc.confluence_id")
        where.append("c.canonical_name = ?")
        params.append(args.confluence)
    if args.outcome:
        where.append("t.outcome = ?")
        params.append(args.outcome)
    if args.instrument:
        where.append("t.instrument LIKE ?")
        params.append(f"%{args.instrument}%")
    if joins:
        sql += " " + " ".join(joins)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY t.trade_date LIMIT ?"
    params.append(args.limit)
    rows = con.execute(sql, params).fetchall()
    dump(rows)


def _cmd_outcome_view(view_name):
    def handler(con, args):
        sql = (
            f"SELECT trade_id, trader, trade_date, instrument, direction, timeframe, "
            f"session_name, outcome, outcome_basis, strategy_key, strategy_name, "
            f"strategy_attribution FROM {view_name}"
        )
        where, params = [], []
        if args.strategy:
            where.append("strategy_key = ?")
            params.append(args.strategy)
        if args.instrument:
            where.append("instrument LIKE ?")
            params.append(f"%{args.instrument}%")
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY trade_date LIMIT ?"
        params.append(args.limit)
        rows = con.execute(sql, params).fetchall()
        dump(rows)
    return handler


cmd_wins = _cmd_outcome_view("wins")
cmd_losses = _cmd_outcome_view("losses")
cmd_breakevens = _cmd_outcome_view("breakevens")


def cmd_message(con, args):
    row = con.execute("SELECT * FROM messages WHERE message_id = ?", (args.message_id,)).fetchone()
    if not row:
        print(f"No message with id {args.message_id}", file=sys.stderr)
        return
    print(json.dumps(dict(row), indent=2, ensure_ascii=False))


def cmd_sql(con, args):
    q = args.query.strip()
    if not q.lower().startswith("select"):
        print("Only SELECT statements are allowed through this escape hatch.", file=sys.stderr)
        sys.exit(1)
    rows = con.execute(q).fetchall()
    dump(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=None, help="Path to the sqlite database (auto-detected by default)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("search", help="Full-text search over Discord messages")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("findings", help="List curated rejection-block findings (optionally by facet)")
    p.add_argument("--facet", default=None, choices=[
        "timing", "high_probability", "identification", "invalidation",
        "low_probability", "instrument_comparison", "other",
    ])
    p.set_defaults(func=cmd_findings)

    p = sub.add_parser("qa", help="Search curated Q&A pairs")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_qa)

    p = sub.add_parser("strategy", help="Full strategy card + evidence + win/loss outcomes for one strategy_key")
    p.add_argument("strategy_key")
    p.set_defaults(func=cmd_strategy)

    p = sub.add_parser("strategies", help="List all strategies (summary)")
    p.add_argument("--family", default=None, help="Filter by strategy family, e.g. rejection-block, ifvg")
    p.add_argument("--status", default=None, choices=["documented", "provisional_derived", "planned"])
    p.set_defaults(func=cmd_strategies)

    p = sub.add_parser("strategy-report", help="Wins/losses/win_rate per strategy -- the 'highest probability strategy' report")
    p.add_argument("--min-n", type=int, default=0, help="Minimum decided (win+loss) trades to include")
    p.set_defaults(func=cmd_strategy_report)

    p = sub.add_parser("confluence-stats", help="Win/loss counts per confluence (curated confluences table)")
    p.add_argument("--min-n", type=int, default=1, help="Minimum total resolved mentions to include")
    p.set_defaults(func=cmd_confluence_stats)

    p = sub.add_parser("trades", help="Filter curated trade episodes (any outcome)")
    p.add_argument("--confluence", default=None)
    p.add_argument("--outcome", default=None, choices=["win", "loss", "breakeven", "unknown"])
    p.add_argument("--instrument", default=None)
    p.add_argument("--limit", type=int, default=25)
    p.set_defaults(func=cmd_trades)

    for name, handler in (("wins", cmd_wins), ("losses", cmd_losses), ("breakevens", cmd_breakevens)):
        p = sub.add_parser(name, help=f"Filter {name} (outcome-filtered trades, joined to strategy if attributed)")
        p.add_argument("--strategy", default=None, help="Filter by strategy_key, e.g. 10am-key-open-rb")
        p.add_argument("--instrument", default=None)
        p.add_argument("--limit", type=int, default=25)
        p.set_defaults(func=handler)

    p = sub.add_parser("message", help="Fetch one message by Discord message_id")
    p.add_argument("message_id")
    p.set_defaults(func=cmd_message)

    p = sub.add_parser("sql", help="Run an arbitrary read-only SELECT query")
    p.add_argument("query")
    p.set_defaults(func=cmd_sql)

    args = parser.parse_args()
    con = connect(args.db)
    try:
        args.func(con, args)
    finally:
        con.close()


if __name__ == "__main__":
    main()
