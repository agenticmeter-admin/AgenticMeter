# AgenticMeter

**Find the bugs that waste money in your agent runs.**

AgenticMeter is a linter for AI agent runs. Wrap your agent, run it, and get told
what went wrong and what it cost: tool loops, duplicate LLM calls, context bloat,
failures your agent quietly swallowed. You don't have to dig through a trace to find them.

It runs locally. Traces stay in a SQLite file on your machine, there is no account
or cloud service, and the core has zero dependencies.

## Try it in 10 seconds

```bash
pip install agenticmeter-cli
agenticmeter demo
```

`demo` runs a simulated travel agent (no API keys, no network) with the bugs real
agents ship with:

```
▸ travel_agent — 11 steps · 28.2k tok · $0.0739 · 0.7s

⚠ One step = 69% of this run's cost
    'openai.chat' cost $0.0510 of $0.0739 total.
    → optimize or cache that single call before anything else.

⚠ Tool failed 1x but the run succeeded anyway
    fetch_hotel_prices errored mid-run; the agent recovered, so nothing surfaced.
    → check whether the recovery path is actually correct, not just quiet.

⚠ Context grew 1,200 → 18,000 tokens
    prompt size grew 15.0x across 5 calls (~$0.0420 of re-sent history on that call)
    → trim or summarize history between steps to stop the quadratic creep.

⚠ Same tool called 4x with identical args
    'search_flights' ran 4 times with the same input — likely a loop or a missing cache.
    → add a cache, or fix the condition that re-calls it.
...
```

Every warning links to the exact spans that prove it. Nothing is guessed.

## Lint your own agent

```python
import agenticmeter as am

am.configure(sink="sqlite")   # persist runs to ~/.agenticmeter/traces.db

@am.tool
def search(query): ...

@am.meter
def agent(task):
    ...

agent("plan my launch week")  # warnings print to stderr when the run ends
```

OpenAI, Anthropic and LangChain calls are captured automatically when those
libraries are installed, with tokens and cost per call. For anything else, mark
steps by hand:

```python
with am.span("retrieve", am.SpanType.RETRIEVAL):
    ...
```

Clean runs stay silent. Pass `summary=True` to always print, or `summary=False` to never print.

## What it catches

| Check | Fires when |
|---|---|
| Silent tool failure | a tool errored but the run reported success |
| Cost concentration | one step is ≥ 60% of the run's cost (across ≥ 3 priced steps) |
| Duplicate LLM calls | the identical prompt was paid for ≥ 2 times |
| Context bloat | the prompt grew ≥ 3x and by ≥ 2k tokens over the run |
| Tool loop | the same tool was called ≥ 3 times with identical arguments |
| Time bottleneck | ≥ 70% of step time went to retrieval, tools or custom code |

Thresholds live in `agenticmeter.analysis.insights.Config`. Very short runs
(fewer than two steps, or under 50ms of work) don't trigger the time check.

## Look back at runs

```bash
agenticmeter runs            # recent runs: steps, tokens, cost
agenticmeter show            # latest run: trace tree + warnings
agenticmeter show <id>       # a specific run (id prefix is fine)
agenticmeter ui              # local web viewer at http://127.0.0.1:4319
agenticmeter demo --save     # keep the demo run so you can explore it in `show` / `ui`
```

In the viewer, clicking a warning highlights the steps that caused it.

## Design rules

- **Zero-dependency core.** Integrations are optional extras:
  `pip install "agenticmeter-cli[openai,anthropic,langchain]"`.
- **Local-first.** No telemetry, no network calls. Your traces never leave your machine.
- **Fail open.** If AgenticMeter hits an internal error, it drops the span and logs
  once. It never crashes or slows your agent.
- **Secrets scrubbed by default.** Inputs and outputs are redacted before they
  are stored (`redact="scrub"`; also `"truncate"`, `"off"` or your own function).
- **Prices are configurable.** Override or add models with `am.cost.set_prices({...})`.

## Install notes

The PyPI package is `agenticmeter-cli`. The import name and command are both
`agenticmeter`. Requires Python 3.10+.

## Development

```bash
pip install -e ".[openai,anthropic]"
python smoke_test.py       # capture layer
python behavior_test.py    # detectors: messy run fires all, clean and trivial runs fire none
```

See [CONTRIBUTING.md](CONTRIBUTING.md). MIT licensed.
