"""Anthropic instrumentation — patches messages.create (sync + async + stream).

Same double-counting rule as the OpenAI patch: when nested under a framework
span, enrich the owner instead of emitting a duplicate.
Anthropic returns usage as input_tokens / output_tokens.
Patch points are version-sensitive; verify against your installed anthropic version.
"""
from __future__ import annotations

import functools

from .. import context as ctx
from ..cost import cost_usd
from ..span import SpanType
from ..tracer import tracer

_originals = {}


def _apply(resp, fallback_model=None):
    model = getattr(resp, "model", None) or fallback_model
    usage = getattr(resp, "usage", None)
    pt = getattr(usage, "input_tokens", None) if usage else None
    cmp = getattr(usage, "output_tokens", None) if usage else None
    return dict(model=model, prompt_tokens=pt, completion_tokens=cmp,
                cost_usd=cost_usd(model, pt, cmp))


def _text(resp):
    try:
        blocks = getattr(resp, "content", None)
        if blocks and getattr(blocks[0], "text", None):
            return blocks[0].text
    except Exception:
        pass
    return None


def _enrich_owner(owner_id, resp):
    owner = tracer._open.get(owner_id)
    if owner is not None:
        tracer.enrich(owner, **_apply(resp, owner.model))


def _finish(span, resp):
    tracer.end_span(span, output=_text(resp), **_apply(resp, span.model))


def _wrap(orig, is_async):
    def _enter(kwargs):
        framework = ctx.in_framework_llm.get()
        owner_id = ctx.current_span.get() if framework else None
        span = None if framework else tracer.start_span(
            SpanType.LLM, name="anthropic.messages",
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
        _finish(span, resp) if span is not None else _enrich_owner(owner_id, resp)
        return resp

    w = async_wrapper if is_async else sync_wrapper
    w._am_patched = True
    return w


def patch():
    try:
        from anthropic.resources import messages as m
    except ImportError:
        return
    for cls_name, is_async in (("Messages", False), ("AsyncMessages", True)):
        cls = getattr(m, cls_name, None)
        if cls is None or getattr(cls.create, "_am_patched", False):
            continue
        _originals[cls_name] = cls.create
        cls.create = _wrap(cls.create, is_async)


def unpatch():
    try:
        from anthropic.resources import messages as m
    except ImportError:
        return
    for cls_name, orig in _originals.items():
        getattr(m, cls_name).create = orig
    _originals.clear()
