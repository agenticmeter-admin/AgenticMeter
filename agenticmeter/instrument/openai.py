"""OpenAI instrumentation.

Patches the lowest stable call site (chat.completions.create) so it catches the
call whether the user invokes it directly OR a framework calls it underneath.

Double-counting rule: if a framework (e.g. LangChain) already opened an LLM span
(context.in_framework_llm is True), we do NOT create our own span — we ENRICH the
framework's span with the exact token usage from the response.

Patch points are version-sensitive; verify against your installed openai version.
"""
from __future__ import annotations

import functools

from .. import context as ctx
from ..cost import cost_usd
from ..span import SpanType
from ..tracer import tracer

_originals = {}


def _usage_fields(resp):
    model = getattr(resp, "model", None)
    if model is None and isinstance(resp, dict):
        model = resp.get("model")
    usage = getattr(resp, "usage", None)
    if usage is None and isinstance(resp, dict):
        usage = resp.get("usage")
    pt = getattr(usage, "prompt_tokens", None) if usage else None
    cmp = getattr(usage, "completion_tokens", None) if usage else None
    if isinstance(usage, dict):
        pt, cmp = usage.get("prompt_tokens"), usage.get("completion_tokens")
    return model, pt, cmp


def _apply(resp, fallback_model=None):
    model, pt, cmp = _usage_fields(resp)
    model = model or fallback_model
    return dict(model=model, prompt_tokens=pt, completion_tokens=cmp,
                cost_usd=cost_usd(model, pt, cmp))


def _safe_choice(resp):
    try:
        choices = getattr(resp, "choices", None)
        if choices:
            return choices[0].message.content
    except Exception:
        pass
    return None


def _enrich_owner(owner_id, resp):
    owner = tracer._open.get(owner_id)
    if owner is not None:
        tracer.enrich(owner, **_apply(resp, owner.model))


def _finish(span, resp):
    tracer.end_span(span, output=_safe_choice(resp), **_apply(resp, span.model))


class _StreamProxy:
    """Closes the span (or enriches the owner) when the stream ends; usage is read
    from the final chunk — requires stream_options={'include_usage': True}."""
    def __init__(self, stream, span, owner_id):
        self._stream, self._span, self._owner_id, self._last = stream, span, owner_id, None

    def _done(self):
        if self._span is not None:
            _finish(self._span, self._last)
        else:
            _enrich_owner(self._owner_id, self._last)

    def __iter__(self):
        try:
            for chunk in self._stream:
                self._last = chunk
                yield chunk
        finally:
            self._done()

    async def __aiter__(self):
        try:
            async for chunk in self._stream:
                self._last = chunk
                yield chunk
        finally:
            self._done()


def _wrap(orig, is_async):
    def _enter(kwargs):
        framework = ctx.in_framework_llm.get()
        owner_id = ctx.current_span.get() if framework else None
        span = None if framework else tracer.start_span(
            SpanType.LLM, name="openai.chat",
            model=kwargs.get("model"), input=kwargs.get("messages"))
        return owner_id, span

    @functools.wraps(orig)
    def sync_wrapper(self, *args, **kwargs):
        owner_id, span = _enter(kwargs)
        try:
            resp = orig(self, *args, **kwargs)
        except Exception as e:
            if span is not None:
                tracer.end_span(span, status="error", error=repr(e))
            raise
        if kwargs.get("stream"):
            return _StreamProxy(resp, span, owner_id)
        _finish(span, resp) if span is not None else _enrich_owner(owner_id, resp)
        return resp

    @functools.wraps(orig)
    async def async_wrapper(self, *args, **kwargs):
        owner_id, span = _enter(kwargs)
        try:
            resp = await orig(self, *args, **kwargs)
        except Exception as e:
            if span is not None:
                tracer.end_span(span, status="error", error=repr(e))
            raise
        if kwargs.get("stream"):
            return _StreamProxy(resp, span, owner_id)
        _finish(span, resp) if span is not None else _enrich_owner(owner_id, resp)
        return resp

    w = async_wrapper if is_async else sync_wrapper
    w._am_patched = True
    return w


def patch():
    try:
        from openai.resources.chat import completions as c
    except ImportError:
        return  # openai not installed -> no-op
    for cls_name, is_async in (("Completions", False), ("AsyncCompletions", True)):
        cls = getattr(c, cls_name, None)
        if cls is None or getattr(cls.create, "_am_patched", False):
            continue
        _originals[cls_name] = cls.create
        cls.create = _wrap(cls.create, is_async)


def unpatch():
    try:
        from openai.resources.chat import completions as c
    except ImportError:
        return
    for cls_name, orig in _originals.items():
        getattr(c, cls_name).create = orig
    _originals.clear()
