"""agenticmeter CLI — look back at persisted runs from the terminal.

    agenticmeter runs              list recent runs
    agenticmeter show [trace_id]   show a run's trace tree + behavior warnings
                                   (defaults to the most recent run)
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime

from .sinks.sqlite import SQLiteSink, DEFAULT_PATH
from .span import SpanType
from .analysis.insights import analyze, format_insights, run_header


def _fmt_time(ts: float) -> str:
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "?"


def _short(usd: float) -> str:
    return f"${usd:.4f}" if usd >= 0.0001 else f"${usd:.6f}"


def cmd_runs(sink, args):
    runs = sink.list_runs(limit=args.limit)
    if not runs:
        print("No runs recorded yet. Run a @meter-wrapped agent first.")
        return
    print(f"{'WHEN':<17}{'RUN':<22}{'STEPS':>6}{'TOKENS':>9}{'COST':>10}  {'ID'}")
    for r in runs:
        flag = "" if r["status"] == "ok" else " ✗"
        print(f"{_fmt_time(r['start']):<17}{r['name'][:21]:<22}{r['steps']:>6}"
              f"{r['tokens']:>9}{_short(r['cost']):>10}  {r['trace_id'][:12]}{flag}")


def cmd_show(sink, args):
    trace_id = args.trace_id or sink.latest_run()
    if not trace_id:
        print("No runs recorded yet.")
        return
    spans = sink.get_trace(trace_id)
    if not spans:
        # allow prefix match
        for r in sink.list_runs(limit=100):
            if r["trace_id"].startswith(trace_id):
                spans = sink.get_trace(r["trace_id"]); break
    if not spans:
        print(f"No run found for '{trace_id}'."); return

    print(run_header(spans) + "\n")
    _print_tree(spans)
    print()
    insights = analyze(spans)
    print(format_insights(insights))


def _print_tree(spans):
    by_id = {s.span_id: s for s in spans}
    children = {}
    root = None
    for s in spans:
        children.setdefault(s.parent_id, []).append(s)
        if s.parent_id is None:
            root = s
    if root is None:
        return

    badge = {SpanType.LLM: "llm ", SpanType.TOOL: "tool", SpanType.AGENT: "agnt",
             SpanType.RETRIEVAL: "retr", SpanType.CUSTOM: "cust"}

    def walk(node, depth):
        for c in sorted(children.get(node.span_id, []), key=lambda s: s.start):
            b = badge.get(c.type, "    ")
            metric = ""
            if c.type == SpanType.LLM and c.total_tokens:
                metric = f"{c.total_tokens} tok"
                if c.cost_usd:
                    metric += f" · {_short(c.cost_usd)}"
            elif c.duration_ms:
                metric = f"{c.duration_ms:.0f}ms"
            flag = "" if c.status == "ok" else "  ✗"
            print(f"  {'  '*depth}{b}  {c.name:<22}{metric}{flag}")
            walk(c, depth + 1)

    walk(root, 0)


def main(argv=None):
    p = argparse.ArgumentParser(prog="agenticmeter")
    p.add_argument("--db", default=DEFAULT_PATH, help="path to the traces db")
    sub = p.add_subparsers(dest="cmd")

    pr = sub.add_parser("runs", help="list recent runs")
    pr.add_argument("-n", "--limit", type=int, default=20)

    ps = sub.add_parser("show", help="show a run's trace + warnings")
    ps.add_argument("trace_id", nargs="?", default=None)

    pu = sub.add_parser("ui", help="open the local web viewer")
    pu.add_argument("-p", "--port", type=int, default=4319)
    pu.add_argument("--no-open", action="store_true", help="don't auto-open the browser")

    args = p.parse_args(argv)

    if args.cmd == "ui":
        from .server import serve
        serve(db_path=args.db, port=args.port, open_browser=not args.no_open)
        return

    sink = SQLiteSink(args.db)

    if args.cmd == "runs":
        cmd_runs(sink, args)
    elif args.cmd == "show":
        cmd_show(sink, args)
    else:
        p.print_help()


if __name__ == "__main__":
    main()
