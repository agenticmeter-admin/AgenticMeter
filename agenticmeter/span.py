"""The Span — the single object everything else is derived from.

Field names follow OpenTelemetry GenAI semantic conventions where they exist,
so a custom tracer today can graduate to the real OTel SDK later without
reshaping the data.
"""
from __future__ import annotations

import enum
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any


class SpanType(str, enum.Enum):
    LLM = "llm"
    TOOL = "tool"
    AGENT = "agent"          # a chain / sub-agent / overall run boundary
    RETRIEVAL = "retrieval"
    CUSTOM = "custom"


def _id() -> str:
    return uuid.uuid4().hex[:16]


@dataclass
class Span:
    type: SpanType
    name: str
    trace_id: str                              # the run this belongs to
    span_id: str = field(default_factory=_id)
    parent_id: str | None = None               # builds the tree

    start: float = field(default_factory=time.time)
    end: float | None = None
    status: str = "ok"                         # "ok" | "error"
    error: str | None = None

    input: Any = None                          # redacted before it leaves the process
    output: Any = None

    # llm-only
    model: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cost_usd: float | None = None

    attributes: dict = field(default_factory=dict)   # OTel-style extras

    # ---- helpers -------------------------------------------------------
    @property
    def duration_ms(self) -> float | None:
        if self.end is None:
            return None
        return round((self.end - self.start) * 1000, 1)

    @property
    def total_tokens(self) -> int | None:
        if self.prompt_tokens is None and self.completion_tokens is None:
            return None
        return (self.prompt_tokens or 0) + (self.completion_tokens or 0)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["type"] = self.type.value
        d["duration_ms"] = self.duration_ms
        d["total_tokens"] = self.total_tokens
        return d


# A sentinel returned when the tracer itself errors, so callers never crash.
NULL_SPAN = Span(type=SpanType.CUSTOM, name="<null>", trace_id="<null>",
                 span_id="<null>")
