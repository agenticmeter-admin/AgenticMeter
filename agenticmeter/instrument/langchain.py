"""LangChain instrumentation.

LangChain hands every event a run_id and parent_run_id. We build the tree from
THOSE (not contextvars), because LangChain's async/threaded execution won't
always line up with a contextvar stack.

While an LLM span is open we set context.in_framework_llm so the OpenAI/Anthropic
SDK patches enrich this span with exact usage instead of emitting a duplicate.

Register globally when a run opens so users don't have to pass callbacks=[...].
"""
from __future__ import annotations

from uuid import UUID

from .. import context as ctx
from ..cost import cost_usd
from ..span import SpanType, _id
from ..tracer import tracer

try:
    from langchain_core.callbacks import BaseCallbackHandler
    _HAVE_LC = True
except ImportError:  # langchain not installed
    BaseCallbackHandler = object  # type: ignore
    _HAVE_LC = False


class AgenticMeterHandler(BaseCallbackHandler):
    def __init__(self):
        self._spans: dict[UUID, object] = {}        # run_id -> Span
        self._fw_tokens: dict[UUID, object] = {}     # run_id -> in_framework_llm token

    # map LangChain's run_id tree onto our parent_id tree
    def _start(self, run_id, parent_run_id, type, name, input=None, **f):
        parent = self._spans.get(parent_run_id)
        parent_id = parent.span_id if parent is not None else ctx.current_span.get()
        # force the contextvar so nested SDK calls attach correctly
        if parent_id is not None:
            ctx.current_span.set(parent_id)
        span = tracer.start_span(type, name=name, input=input, parent_id=parent_id, **f)
        self._spans[run_id] = span
        return span

    # ---- LLM ----
    def on_llm_start(self, serialized, prompts, *, run_id, parent_run_id=None, **kw):
        name = (serialized or {}).get("name") or "llm"
        self._start(run_id, parent_run_id, SpanType.LLM, name, input=prompts,
                    model=(kw.get("invocation_params") or {}).get("model"))
        self._fw_tokens[run_id] = ctx.in_framework_llm.set(True)

    def on_chat_model_start(self, serialized, messages, *, run_id, parent_run_id=None, **kw):
        self.on_llm_start(serialized, messages, run_id=run_id,
                          parent_run_id=parent_run_id, **kw)

    def on_llm_end(self, response, *, run_id, **kw):
        span = self._spans.pop(run_id, None)
        tok = self._fw_tokens.pop(run_id, None)
        if tok is not None:
            try:
                ctx.in_framework_llm.reset(tok)
            except (LookupError, ValueError):
                pass
        if span is None:
            return
        # token usage lives in llm_output.usage / token_usage depending on provider
        out = getattr(response, "llm_output", None) or {}
        usage = out.get("token_usage") or out.get("usage") or {}
        pt = usage.get("prompt_tokens") or usage.get("input_tokens")
        cmp = usage.get("completion_tokens") or usage.get("output_tokens")
        model = out.get("model_name") or span.model
        # only set if the SDK patch didn't already enrich it
        fields = {}
        if span.prompt_tokens is None and pt is not None:
            fields.update(prompt_tokens=pt, completion_tokens=cmp,
                          model=model, cost_usd=cost_usd(model, pt, cmp))
        tracer.end_span(span, **fields)

    def on_llm_error(self, error, *, run_id, **kw):
        span = self._spans.pop(run_id, None)
        tok = self._fw_tokens.pop(run_id, None)
        if tok is not None:
            try:
                ctx.in_framework_llm.reset(tok)
            except (LookupError, ValueError):
                pass
        if span is not None:
            tracer.end_span(span, status="error", error=repr(error))

    # ---- tools ----
    def on_tool_start(self, serialized, input_str, *, run_id, parent_run_id=None, **kw):
        name = (serialized or {}).get("name") or "tool"
        self._start(run_id, parent_run_id, SpanType.TOOL, name, input=input_str)

    def on_tool_end(self, output, *, run_id, **kw):
        span = self._spans.pop(run_id, None)
        if span is not None:
            tracer.end_span(span, output=output)

    def on_tool_error(self, error, *, run_id, **kw):
        span = self._spans.pop(run_id, None)
        if span is not None:
            tracer.end_span(span, status="error", error=repr(error))

    # ---- chains / agents ----
    def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None, **kw):
        name = (serialized or {}).get("name") or "chain"
        self._start(run_id, parent_run_id, SpanType.AGENT, name, input=inputs)

    def on_chain_end(self, outputs, *, run_id, **kw):
        span = self._spans.pop(run_id, None)
        if span is not None:
            tracer.end_span(span, output=outputs)

    def on_chain_error(self, error, *, run_id, **kw):
        span = self._spans.pop(run_id, None)
        if span is not None:
            tracer.end_span(span, status="error", error=repr(error))


_global_handler: AgenticMeterHandler | None = None


def register_global():
    """Install the handler so every LangChain run is captured without explicit
    callbacks=[...]. Safe no-op if LangChain isn't installed."""
    global _global_handler
    if not _HAVE_LC:
        return None
    try:
        from langchain_core.tracers.context import register_configure_hook
        from contextvars import ContextVar
        if _global_handler is None:
            _global_handler = AgenticMeterHandler()
            _var: ContextVar = ContextVar("am_lc_handler", default=_global_handler)
            register_configure_hook(_var, inheritable=True)
    except Exception:
        # fall back: caller can pass get_handler() in config={"callbacks": [...]}
        if _global_handler is None:
            _global_handler = AgenticMeterHandler()
    return _global_handler


def get_handler() -> AgenticMeterHandler:
    return register_global() or AgenticMeterHandler()
