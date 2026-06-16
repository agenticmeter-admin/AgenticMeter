"""Public capture API for hand-rolled agents and anything the auto-instrument
surfaces don't cover:

    @meter                      # wrap the whole run
    def agent(task): ...

    with meter.span("retrieve", SpanType.RETRIEVAL): ...

    @meter.tool                 # mark a function as a tool step
    def search(q): ...
"""
from __future__ import annotations

import functools
from contextlib import contextmanager
from typing import Any

from .redact import make_redactor
from .span import SpanType
from .tracer import tracer

_safe = make_redactor("scrub")  # used for manual input/output capture pre-tracer


@contextmanager
def span(name: str, type: SpanType = SpanType.CUSTOM, *, input: Any = None, **fields):
    s = tracer.start_span(type, name=name, input=input, **fields)
    try:
        yield s
    except Exception as e:
        tracer.end_span(s, status="error", error=repr(e))
        raise
    else:
        tracer.end_span(s)


def tool(fn=None, *, name: str | None = None):
    def deco(f):
        @functools.wraps(f)
        def inner(*a, **k):
            with span(name or f.__name__, SpanType.TOOL,
                      input={"args": a, "kwargs": k}) as s:
                out = f(*a, **k)
                s.output = out          # redacted on end via tracer
                tracer.end_span(s, output=out)
                # prevent double end: mark so context-manager exit is a no-op
                return out
        return inner
    return deco(fn) if fn else deco


class _Meter:
    """Callable run decorator with attached helpers (meter, meter.span, meter.tool)."""
    span = staticmethod(span)
    tool = staticmethod(tool)

    def __call__(self, fn=None, *, name: str | None = None):
        def deco(f):
            @functools.wraps(f)
            def inner(*a, **k):
                # opening a run also turns on framework auto-capture
                from .instrument import langchain as _lc
                _lc.register_global()
                run = tracer.start_run(name or f.__name__,
                                       input={"args": a, "kwargs": k})
                try:
                    out = f(*a, **k)
                except Exception as e:
                    tracer.end_span(run, status="error", error=repr(e))
                    raise
                tracer.end_span(run, output=out)
                return out
            return inner
        return deco(fn) if fn else deco


meter = _Meter()
