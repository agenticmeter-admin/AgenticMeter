"""Sinks receive finished spans. The base defines the contract; MemorySink is the
zero-dependency default used in tests (the real SQLite sink implements the same
interface)."""
from __future__ import annotations

from collections import defaultdict

from ..span import Span


class Sink:
    def write(self, span: Span) -> None: ...
    def flush(self) -> None: ...
    def get_trace(self, trace_id: str) -> list[Span]: return []


class MemorySink(Sink):
    def __init__(self):
        self.spans: list[Span] = []
        self.by_trace: dict[str, list[Span]] = defaultdict(list)

    def write(self, span: Span) -> None:
        self.spans.append(span)
        self.by_trace[span.trace_id].append(span)

    def flush(self) -> None:
        pass

    def get_trace(self, trace_id: str) -> list[Span]:
        return list(self.by_trace.get(trace_id, []))

    # build the parent->children tree for one run (what the viewer renders)
    def tree(self, trace_id: str):
        spans = self.by_trace.get(trace_id, [])
        children = defaultdict(list)
        root = None
        for s in spans:
            if s.parent_id is None:
                root = s
            else:
                children[s.parent_id].append(s)
        return root, children
