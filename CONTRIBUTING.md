# Contributing to AgenticMeter

Thanks for your interest in contributing. AgenticMeter is a local-first observability library for AI agents — contributions that keep it simple, dependency-free, and useful are most welcome.

## Ways to contribute

- **Bug reports** — open a GitHub Issue with steps to reproduce
- **Feature requests** — open an Issue describing the use case
- **Code** — open a Pull Request (see below)
- **Documentation** — improvements to README or inline docs
- **Examples** — new agent demos in the repo

## Getting started

```bash
git clone https://github.com/agenticmeter-admin/AgenticMeter.git
cd AgenticMeter
pip install -e ".[openai,anthropic]"
python smoke_test.py   # all checks should pass
```

## Before opening a PR

1. **Run the smoke test** — `python smoke_test.py` must pass with no errors
2. **Keep it focused** — one change per PR
3. **No new mandatory dependencies** — the core library must stay zero-dependency
4. **Match the existing style** — no docstrings on obvious functions, no unnecessary comments

## What we won't merge

- Changes that add required third-party dependencies to the core
- Cloud-only features that break the local-first guarantee
- Large refactors without prior discussion in an Issue

## Project structure

```
agenticmeter/
  __init__.py          # public API: configure(), meter, tool, span, cost
  tracer.py            # span lifecycle, context propagation
  span.py              # Span dataclass + SpanType enum
  decorators.py        # @am.meter and @am.tool
  cost.py              # token → USD pricing
  context.py           # contextvars for run/span tracking
  redact.py            # PII scrubbing
  sinks/
    base.py            # Sink ABC
    sqlite.py          # default persistence
  instrument/
    openai.py          # OpenAI SDK auto-patch
    anthropic.py       # Anthropic SDK auto-patch
    langchain.py       # LangChain callback handler
  analysis/
    insights.py        # 6 behavior detectors
  cli.py               # agenticmeter runs / show / ui
  server.py            # local web UI (stdlib only)
```

## Opening a PR

1. Fork the repo and create a branch: `git checkout -b fix/your-fix`
2. Make your changes
3. Run `python smoke_test.py`
4. Open a PR with a clear description of what and why

We review PRs within a few days. Thank you.
