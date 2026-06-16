# agenticmeter — capture layer (prototype)

Agent-centric, local-first observability. Wrap your agent; get a trace tree of
every step, tool call, and loop with token + cost usage.

## Quick start
```python
import agenticmeter as am
sink = am.configure()          # scrub redaction on, auto-patches OpenAI/Anthropic/LangChain

@am.meter                      # wrap the run
def agent(task):
    with am.span("plan", am.SpanType.LLM):
        ...
    return do_work(task)

agent("plan my launch week")

# inspect the trace tree
root, children = sink.tree(sink.spans[0].trace_id)
```

## What's implemented
- `context.py`   — contextvars spine (current run/span) + `bind_worker` for threads
- `tracer.py`    — start/end spans, build tree, **fail-open**, route to sink
- `span.py`      — OTel-shaped Span + SpanType
- `redact.py`    — scrub secrets + truncate (on by default)
- `cost.py`      — per-model token→$ price map (override with `am.cost.set_prices`)
- `decorators.py`— `@meter`, `with meter.span(...)`, `@meter.tool`
- `instrument/openai.py`     — sync + async + streaming, enrich-not-duplicate
- `instrument/anthropic.py`  — sync + async
- `instrument/langchain.py`  — callback handler, run_id tree, global register
- `sinks/base.py`            — Sink interface + MemorySink (+ tree builder)

## Capture surfaces & the double-counting rule
LLM call usage is captured by SDK patching. When a framework (LangChain) already
opened an LLM span, the SDK patch **enriches that span** with exact token usage
instead of emitting a duplicate — driven by `context.in_framework_llm`.

## Not yet built (next steps)
- SQLite sink + background async flush queue (`flush.py`)
- `agenticmeter ui` local viewer
- loop / cycle detection (`analysis/loops.py`)
- cloud exporter

## Run the tests
```bash
python3 smoke_test.py
```

## Behavior layer (the product)
`analysis/insights.py` — six pure detectors over the span tree, each returning
Insights with evidence (span ids) + an action. Assert-only-what-you-can-prove:

| code | tier | fires when |
|------|------|-----------|
| silent_tool_failures | fact | a tool errored but the run returned ok |
| cost_concentration   | fact | one step >= 60% of run cost (>= 3 priced steps) |
| repeated_llm_calls   | fact | identical prompt billed >= 2x |
| context_growth       | fact | peak prompt >= 3x first (and >= 2k extra tokens) |
| repeated_tool_calls  | pattern | same tool + same args >= 3x (the provable "loop") |
| time_concentration   | fact | >= 70% of step time in retrieval/tool/custom |

```python
from agenticmeter.analysis.insights import analyze, format_insights
print(format_insights(analyze(spans)))   # spans = one trace's span list
```

Run the demo / tests:
```bash
python3 behavior_demo.py     # messy run -> all six warnings
python3 behavior_test.py     # messy fires all six, clean fires none
```

## Seeing the output (Level 0 + 1)
Level 0 — every `@meter` run prints its behavior summary when it closes (warnings
only by default, so clean runs stay silent):
```python
import agenticmeter as am
am.configure(sink="sqlite", summary="auto")   # "auto" | True | False

@am.meter
def agent(task): ...
agent("...")     # -> prints ▸ run header + any ⚠ warnings to stderr
```

Level 1 — runs persist to ~/.agenticmeter/traces.db; replay them from the terminal:
```bash
agenticmeter runs            # list recent runs (or: python -m agenticmeter runs)
agenticmeter show            # latest run: trace tree + warnings
agenticmeter show <id>       # a specific run (prefix ok)
```

Try it: `python3 level01_demo.py`, then `python3 -m agenticmeter --db /tmp/agenticmeter_demo.db show`.

## Install (editable)
```bash
pip install -e .             # registers the `agenticmeter` command
```
This also avoids the import-shadowing trap: run from anywhere, edits picked up live.

## Local web viewer (Level 2)
```bash
agenticmeter ui              # opens http://127.0.0.1:4319 in your browser
agenticmeter ui --port 8080 --no-open
```
Single page: live-polling run list (left), selected run's behavior findings + trace
tree (right). Click any ⚠ finding and it highlights + scrolls to the offending
steps in the trace — diagnosis you can verify in one click. Stdlib only, no deps.
