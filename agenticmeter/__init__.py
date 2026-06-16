"""agenticmeter — agent-centric, local-first observability.

    import agenticmeter as am
    am.configure(sink="sqlite")      # persist runs; print a summary when issues appear

    @am.meter
    def agent(task): ...
"""
from __future__ import annotations

from .decorators import meter, span, tool
from .span import Span, SpanType
from .tracer import tracer
from .sinks.base import MemorySink
from . import cost

__all__ = ["meter", "span", "tool", "configure", "Span", "SpanType",
           "tracer", "MemorySink", "auto_instrument", "cost"]

_default_sink = None


def configure(sink=None, redact: str = "scrub", auto: bool = True,
              summary="auto", stream=None):
    """Set up agenticmeter. Call once at startup.

    sink    - "memory" (default) | "sqlite" | a Sink instance
    redact  - "scrub" (default) | "truncate" | "off" | callable
    auto    - patch OpenAI/Anthropic + register LangChain handler if installed
    summary - "auto" (print warnings only) | True (always) | False (never)
    stream  - where the summary prints (default: stderr)
    """
    global _default_sink
    sink = _resolve_sink(sink)
    _default_sink = sink
    tracer.configure(sink=sink, redact=redact, summary=summary, stream=stream)
    if auto:
        auto_instrument()
    return sink


def _resolve_sink(sink):
    if sink is None or sink == "memory":
        return MemorySink()
    if sink == "sqlite":
        from .sinks.sqlite import SQLiteSink
        return SQLiteSink()
    return sink   # already a Sink instance


def auto_instrument():
    """Best-effort patch of every supported surface. Each is a no-op if the
    library isn't installed, so this is always safe to call."""
    from .instrument import openai as _oa, anthropic as _an, langchain as _lc
    _oa.patch()
    _an.patch()
    _lc.register_global()


def get_sink():
    return _default_sink
