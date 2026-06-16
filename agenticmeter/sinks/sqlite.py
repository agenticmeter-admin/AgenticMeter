"""Local-first persistence. Spans are written to a SQLite file (default
~/.agenticmeter/traces.db) so runs survive between processes and the CLI / viewer
can read them back. Writes are synchronous + lock-guarded and fail-open; an async
flush queue is a later optimization, not needed at dev volume.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path

from ..span import Span, SpanType
from .base import Sink

DEFAULT_PATH = os.path.expanduser("~/.agenticmeter/traces.db")

_SCALAR = ["span_id", "trace_id", "parent_id", "type", "name", "start", "end",
           "status", "error", "model", "prompt_tokens", "completion_tokens", "cost_usd"]


class SQLiteSink(Sink):
    def __init__(self, path: str = DEFAULT_PATH):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS spans (
                span_id TEXT PRIMARY KEY, trace_id TEXT, parent_id TEXT,
                type TEXT, name TEXT, start REAL, end REAL,
                status TEXT, error TEXT, model TEXT,
                prompt_tokens INTEGER, completion_tokens INTEGER, cost_usd REAL,
                input TEXT, output TEXT, attributes TEXT
            )""")
        self._conn.execute("CREATE INDEX IF NOT EXISTS ix_trace ON spans(trace_id)")
        self._conn.commit()

    # ---- write -------------------------------------------------------
    def write(self, span: Span) -> None:
        try:
            row = [getattr(span, f) if f != "type" else span.type.value for f in _SCALAR]
            row += [self._enc(span.input), self._enc(span.output),
                    self._enc({k: v for k, v in span.attributes.items()
                               if not k.startswith("_")})]
            with self._lock:
                self._conn.execute(
                    f"INSERT OR REPLACE INTO spans "
                    f"({','.join(_SCALAR)}, input, output, attributes) "
                    f"VALUES ({','.join('?' * (len(_SCALAR) + 3))})", row)
                self._conn.commit()
        except Exception:
            pass   # fail-open: never break the user's agent over a write

    def flush(self) -> None:
        with self._lock:
            self._conn.commit()

    # ---- read --------------------------------------------------------
    def get_trace(self, trace_id: str) -> list[Span]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM spans WHERE trace_id=? ORDER BY start", (trace_id,)
            ).fetchall()
        return [self._to_span(r) for r in rows]

    def list_runs(self, limit: int = 20) -> list[dict]:
        """Recent runs (root spans), newest first, with rollups."""
        with self._lock:
            roots = self._conn.execute(
                "SELECT * FROM spans WHERE parent_id IS NULL ORDER BY start DESC LIMIT ?",
                (limit,)).fetchall()
            out = []
            for r in roots:
                agg = self._conn.execute(
                    "SELECT COUNT(*) n, COALESCE(SUM(cost_usd),0) cost, "
                    "COALESCE(SUM(COALESCE(prompt_tokens,0)+COALESCE(completion_tokens,0)),0) tok "
                    "FROM spans WHERE trace_id=?", (r["trace_id"],)).fetchone()
                out.append({"trace_id": r["trace_id"], "name": r["name"],
                            "start": r["start"], "status": r["status"],
                            "steps": agg["n"] - 1, "cost": agg["cost"], "tokens": agg["tok"]})
            return out

    def latest_run(self) -> str | None:
        runs = self.list_runs(limit=1)
        return runs[0]["trace_id"] if runs else None

    # ---- helpers -----------------------------------------------------
    @staticmethod
    def _enc(v):
        if v is None:
            return None
        try:
            return json.dumps(v, default=str)
        except Exception:
            return json.dumps(str(v))

    @staticmethod
    def _dec(s):
        if s is None:
            return None
        try:
            return json.loads(s)
        except Exception:
            return s

    def _to_span(self, r: sqlite3.Row) -> Span:
        s = Span(type=SpanType(r["type"]), name=r["name"], trace_id=r["trace_id"],
                 span_id=r["span_id"], parent_id=r["parent_id"],
                 start=r["start"], end=r["end"], status=r["status"], error=r["error"],
                 input=self._dec(r["input"]), output=self._dec(r["output"]),
                 model=r["model"], prompt_tokens=r["prompt_tokens"],
                 completion_tokens=r["completion_tokens"], cost_usd=r["cost_usd"],
                 attributes=self._dec(r["attributes"]) or {})
        return s
