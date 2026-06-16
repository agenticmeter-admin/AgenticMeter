"""Tracer — start/end spans, attach them to the current run, hand finished
spans to the active sink. Every public entry point is fail-open: an internal
error logs once and returns a NULL span rather than raising into the user's agent.
"""
from __future__ import annotations

import functools
import logging
import time
from typing import Any

from . import context as ctx
from .redact import make_redactor
from .span import NULL_SPAN, Span, SpanType

log = logging.getLogger("agenticmeter")
_warned = False


def _log_once(exc: Exception) -> None:
    global _warned
    if not _warned:
        log.warning("agenticmeter disabled a span due to an internal error: %r", exc)
        _warned = True


def fail_open(method):
    """Decorator: swallow any internal error, returning a safe fallback."""
    @functools.wraps(method)
    def g(self, *a, **k):
        try:
            return method(self, *a, **k)
        except Exception as e:           # never break the user's agent
            _log_once(e)
            return NULL_SPAN if method.__name__ == "start_span" else None
    return g


class Tracer:
    def __init__(self):
        self._sink = None
        self._redact = make_redactor("scrub")
        self._open: dict[str, Span] = {}     # span_id -> Span (in-flight)
        self._summary = "auto"               # "auto" (warnings only) | True | False
        self._stream = None                  # defaults to sys.stderr

    def configure(self, sink=None, redact=None, summary=None, stream=None):
        if sink is not None:
            self._sink = sink
        if redact is not None:
            self._redact = make_redactor(redact)
        if summary is not None:
            self._summary = summary
        if stream is not None:
            self._stream = stream

    # ---- run boundary --------------------------------------------------
    @fail_open
    def start_run(self, name: str, input: Any = None) -> Span:
        from .span import _id
        trace_id = _id()
        tok_t = ctx.current_trace.set(trace_id)
        run = Span(type=SpanType.AGENT, name=name, trace_id=trace_id,
                   parent_id=None, input=self._redact(input))
        run.attributes["_tok_trace"] = tok_t
        run.attributes["_is_run"] = True
        tok_s = ctx.current_span.set(run.span_id)
        run.attributes["_tok_span"] = tok_s
        self._open[run.span_id] = run
        return run

    # ---- generic span --------------------------------------------------
    @fail_open
    def start_span(self, type: SpanType, name: str, *, input: Any = None,
                   parent_id: str | None = None, **fields) -> Span:
        trace_id = ctx.current_trace.get()
        if trace_id is None:
            return NULL_SPAN                 # not inside a metered run -> no-op
        parent = parent_id if parent_id is not None else ctx.current_span.get()
        s = Span(type=type, name=name, trace_id=trace_id, parent_id=parent,
                 input=self._redact(input), **fields)
        tok = ctx.current_span.set(s.span_id)
        s.attributes["_tok_span"] = tok
        self._open[s.span_id] = s
        return s

    @fail_open
    def end_span(self, span: Span, *, status: str = "ok", error: str | None = None,
                 output: Any = None, **fields) -> None:
        if span is NULL_SPAN or span.end is not None:
            return
        span.end = time.time()
        span.status = status
        span.error = error
        if output is not None:
            span.output = self._redact(output)
        for k, v in fields.items():
            setattr(span, k, v)

        is_run = bool(span.attributes.get("_is_run"))

        tok = span.attributes.pop("_tok_span", None)
        if tok is not None:
            try:
                ctx.current_span.reset(tok)
            except (LookupError, ValueError):
                pass

        if span.attributes.pop("_is_run", False):
            tok_t = span.attributes.pop("_tok_trace", None)
            if tok_t is not None:
                try:
                    ctx.current_trace.reset(tok_t)
                except (LookupError, ValueError):
                    pass

        self._open.pop(span.span_id, None)
        if self._sink is not None:
            try:
                self._sink.write(span)
            except Exception as e:
                _log_once(e)

        if is_run:
            self._emit_summary(span.trace_id)

    def _emit_summary(self, trace_id: str) -> None:
        if self._summary is False or self._sink is None:
            return
        try:
            from .analysis.insights import analyze, format_run_summary
            spans = self._sink.get_trace(trace_id)
            if not spans:
                return
            insights = analyze(spans)
            if not insights and self._summary != True:   # "auto" = warnings only
                return
            import sys
            out = self._stream or sys.stderr
            print("\n" + format_run_summary(spans, insights), file=out)
        except Exception as e:
            _log_once(e)

    # convenience for SDK enrichment (merge tokens/cost into an open span)
    @fail_open
    def enrich(self, span: Span, **fields) -> None:
        for k, v in fields.items():
            if v is not None:
                setattr(span, k, v)


# module-level singleton the whole package shares
tracer = Tracer()
