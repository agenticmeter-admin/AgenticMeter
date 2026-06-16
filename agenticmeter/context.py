"""The spine every capture surface sits on.

The "current run" and "current span" live in contextvars. These propagate into
asyncio tasks automatically, but NOT into threads — so `bind_worker` is provided
for agents that fan tools out across a ThreadPoolExecutor.
"""
from __future__ import annotations

import contextvars
import functools
from typing import Callable

# trace_id of the run we're inside (None = not metered)
current_trace: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "am_trace", default=None
)
# span_id of the innermost open span (the parent for the next span)
current_span: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "am_span", default=None
)
# set True by a framework handler (e.g. LangChain) while it owns an LLM span,
# so the SDK patch ENRICHES that span instead of emitting a duplicate.
in_framework_llm: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "am_in_fw_llm", default=False
)


def bind_worker(fn: Callable) -> Callable:
    """Wrap a callable so it runs with the CURRENT context copied in.

    Use when handing work to threads:
        executor.submit(bind_worker(do_tool), args)
    so spans created in the worker still attach to the active run.
    """
    ctx = contextvars.copy_context()

    @functools.wraps(fn)
    def runner(*args, **kwargs):
        return ctx.run(fn, *args, **kwargs)

    return runner
