"""Behavior layer — the product, not the tracing.

Each detector is a PURE function: trace (list[Span]) -> list[Insight].
No I/O, no mutation, no model calls. Every insight it returns is computed from
the span tree and carries the span ids that prove it (evidence) plus an action.

Design rule (locked): assert only what can be proven, and link to the proof.
Tiers:
  "fact"    - computed, indisputable.
  "pattern" - tightly-defined heuristic with a crisp definition + evidence.
There is no "hypothesis" tier here on purpose; interpretive guesses were cut.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable

from ..span import Span, SpanType
from ..cost import cost_usd


# --------------------------------------------------------------------------- #
@dataclass
class Insight:
    code: str                       # machine id, e.g. "cost_concentration"
    tier: str                       # "fact" | "pattern"
    title: str                      # the one-line ⚠ message (with numbers)
    detail: str                     # one sentence: what + why it matters
    action: str                     # the "...so do X"
    evidence: list[str]             # span_ids to drill into
    score: float = 0.0              # ranking weight (higher = surface first)
    metrics: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {**self.__dict__}


@dataclass
class Config:
    cost_share: float = 0.60        # one step >= 60% of run cost
    cost_min_steps: int = 3         # ...and only meaningful across several steps
    repeat_tool: int = 3            # same tool+args called >= 3x
    repeat_llm: int = 2             # identical llm call >= 2x
    time_share: float = 0.70        # >= 70% of self-time in one span type
    ctx_growth: float = 3.0         # final prompt >= 3x the first
    ctx_min_delta: int = 2000       # ...and at least this many extra tokens
    min_steps: int = 2              # ignore trivially short runs


# --------------------------------------------------------------------------- #
# helpers
def _root(spans: list[Span]) -> Span | None:
    for s in spans:
        if s.parent_id is None:
            return s
    return None


def _dur(s: Span) -> float:
    return (s.end - s.start) if s.end else 0.0


def _self_times(spans: list[Span]) -> dict[str, float]:
    """span_id -> self time (own duration minus children's), to avoid double
    counting nested spans when attributing where time went."""
    children = defaultdict(list)
    for s in spans:
        if s.parent_id:
            children[s.parent_id].append(s)
    out = {}
    for s in spans:
        child = sum(_dur(c) for c in children[s.span_id])
        out[s.span_id] = max(_dur(s) - child, 0.0)
    return out


def _key(s: Span) -> str:
    try:
        payload = json.dumps(s.input, sort_keys=True, default=str)
    except Exception:
        payload = repr(s.input)
    return f"{s.name}|{payload}"


def _short(usd: float | None) -> str:
    if usd is None:
        return "$0"
    return f"${usd:.4f}" if usd >= 0.0001 else f"${usd:.6f}"


# --------------------------------------------------------------------------- #
# 1. cost concentration  (FACT)
def cost_concentration(spans, cfg):
    priced = [s for s in spans if s.cost_usd]
    total = sum(s.cost_usd for s in priced)
    if total <= 0 or len(priced) < cfg.cost_min_steps:
        return []
    top = max(priced, key=lambda s: s.cost_usd)
    share = top.cost_usd / total
    if share < cfg.cost_share:
        return []
    pct = round(share * 100)
    return [Insight(
        code="cost_concentration", tier="fact",
        title=f"One step = {pct}% of this run's cost",
        detail=f"'{top.name}' cost {_short(top.cost_usd)} of {_short(total)} total.",
        action="optimize or cache that single call before anything else.",
        evidence=[top.span_id], score=80 + share * 20,
        metrics={"step": top.name, "step_cost": top.cost_usd,
                 "run_cost": total, "share": round(share, 3)})]


# 2. repeated identical tool calls  (PATTERN — the provable heir to "loop")
def repeated_tool_calls(spans, cfg):
    groups = defaultdict(list)
    for s in spans:
        if s.type == SpanType.TOOL:
            groups[_key(s)].append(s)
    out = []
    for key, members in groups.items():
        if len(members) >= cfg.repeat_tool:
            name = members[0].name
            out.append(Insight(
                code="repeated_tool_calls", tier="pattern",
                title=f"Same tool called {len(members)}x with identical args",
                detail=f"'{name}' ran {len(members)} times with the same input — "
                       f"likely a loop or a missing cache.",
                action="add a cache, or fix the condition that re-calls it.",
                evidence=[m.span_id for m in members],
                score=70 + len(members),
                metrics={"tool": name, "count": len(members)}))
    return out


# 3. time concentration by span type  (FACT)
def time_concentration(spans, cfg):
    self_t = _self_times(spans)
    by_type = defaultdict(float)
    ids_by_type = defaultdict(list)
    total = 0.0
    for s in spans:
        if s.parent_id is None:        # don't attribute the run wrapper / its gaps
            continue
        by_type[s.type] += self_t[s.span_id]
        ids_by_type[s.type].append(s.span_id)
        total += self_t[s.span_id]
    if total <= 0 or not by_type:
        return []
    # LLM/agent dominating wall time is expected and not actionable; only flag
    # when non-LLM work (retrieval, tools, custom) is the bottleneck.
    actionable = {SpanType.RETRIEVAL, SpanType.TOOL, SpanType.CUSTOM}
    candidates = {k: v for k, v in by_type.items() if k in actionable}
    if not candidates:
        return []
    kind, t = max(candidates.items(), key=lambda kv: kv[1])
    share = t / total
    if share < cfg.time_share:
        return []
    pct = round(share * 100)
    return [Insight(
        code="time_concentration", tier="fact",
        title=f"{pct}% of execution time spent in {kind.value}",
        detail=f"{kind.value} took {t*1000:.0f}ms of {total*1000:.0f}ms total wall time.",
        action=f"the bottleneck is {kind.value}; optimize there for the biggest win.",
        evidence=ids_by_type[kind], score=60 + share * 20,
        metrics={"type": kind.value, "share": round(share, 3),
                 "type_ms": round(t * 1000, 1), "total_ms": round(total * 1000, 1)})]


# 4. context window growth  (FACT)
def context_growth(spans, cfg):
    llms = [s for s in spans if s.type == SpanType.LLM and s.prompt_tokens]
    if len(llms) < 2:
        return []
    first = min(llms, key=lambda s: s.start)        # earliest call
    peak = max(llms, key=lambda s: s.prompt_tokens)  # largest context reached
    if peak.start <= first.start or first.prompt_tokens <= 0:
        return []
    delta = peak.prompt_tokens - first.prompt_tokens
    if delta < cfg.ctx_min_delta:
        return []
    ratio = peak.prompt_tokens / first.prompt_tokens
    if ratio < cfg.ctx_growth:
        return []
    last = peak
    est = cost_usd(last.model, delta, 0)
    extra = f" (~{_short(est)} of re-sent history on that call)" if est else ""
    return [Insight(
        code="context_growth", tier="fact",
        title=f"Context grew {first.prompt_tokens:,} → {last.prompt_tokens:,} tokens",
        detail=f"prompt size grew {ratio:.1f}x across {len(llms)} calls{extra}; "
               f"every step re-sends the whole history.",
        action="trim or summarize history between steps to stop the quadratic creep.",
        evidence=[first.span_id, last.span_id], score=65 + min(ratio, 10),
        metrics={"first_tokens": first.prompt_tokens, "last_tokens": last.prompt_tokens,
                 "ratio": round(ratio, 2), "delta_tokens": delta,
                 "est_resent_cost": est})]


# 5. repeated identical LLM calls  (FACT — pure waste)
def repeated_llm_calls(spans, cfg):
    groups = defaultdict(list)
    for s in spans:
        if s.type == SpanType.LLM and s.input is not None:
            groups[_key(s)].append(s)
    out = []
    for key, members in groups.items():
        if len(members) >= cfg.repeat_llm:
            wasted = sum(m.cost_usd or 0 for m in members[1:])
            cost_note = f" — wasted {_short(wasted)}" if wasted else ""
            out.append(Insight(
                code="repeated_llm_calls", tier="fact",
                title=f"Identical LLM call made {len(members)}x{cost_note}",
                detail=f"the same prompt to '{members[0].name}' was paid for "
                       f"{len(members)} times.",
                action="cache by prompt hash; the repeat calls are free to eliminate.",
                evidence=[m.span_id for m in members],
                score=75 + (wasted * 1000 if wasted else len(members)),
                metrics={"call": members[0].name, "count": len(members),
                         "wasted_cost": round(wasted, 6)}))
    return out


# 6. silent tool failures  (FACT)
def silent_tool_failures(spans, cfg):
    root = _root(spans)
    if root is None or root.status != "ok":
        return []                       # only "silent" if the run looked fine
    failed = [s for s in spans if s.type == SpanType.TOOL and s.status == "error"]
    if not failed:
        return []
    names = ", ".join(sorted({s.name for s in failed}))
    return [Insight(
        code="silent_tool_failures", tier="fact",
        title=f"Tool failed {len(failed)}x but the run succeeded anyway",
        detail=f"{names} errored mid-run; the agent recovered, so nothing surfaced.",
        action="check whether the recovery path is actually correct, not just quiet.",
        evidence=[s.span_id for s in failed], score=85 + len(failed),
        metrics={"failed_count": len(failed), "tools": sorted({s.name for s in failed})})]


# --------------------------------------------------------------------------- #
DETECTORS: list[Callable] = [
    silent_tool_failures,   # correctness first
    cost_concentration,
    repeated_llm_calls,
    context_growth,
    repeated_tool_calls,
    time_concentration,
]


def analyze(spans: list[Span], cfg: Config | None = None) -> list[Insight]:
    """Run every detector over one trace and return insights, ranked."""
    cfg = cfg or Config()
    found: list[Insight] = []
    for detect in DETECTORS:
        try:
            found.extend(detect(spans, cfg))
        except Exception:
            continue                    # a broken detector never breaks analysis
    found.sort(key=lambda i: i.score, reverse=True)
    return found


def format_insights(insights: list[Insight]) -> str:
    if not insights:
        return "✓ No behavior issues detected."
    lines = []
    for i in insights:
        tag = "·fact " if i.tier == "fact" else "·patt "
        lines.append(f"⚠ {i.title}")
        lines.append(f"    {i.detail}")
        lines.append(f"    → {i.action}")
        lines.append(f"    [{tag.strip()}] evidence: {', '.join(i.evidence[:6])}")
        lines.append("")
    return "\n".join(lines)


def _fmt_tokens(n: int) -> str:
    if n >= 1000:
        return f"{n/1000:.1f}k"
    return str(n)


def run_header(spans: list[Span]) -> str:
    """One line: run name · steps · tokens · cost · duration."""
    root = _root(spans)
    name = root.name if root else "run"
    steps = sum(1 for s in spans if s.parent_id is not None)
    tokens = sum((s.total_tokens or 0) for s in spans)
    cost = sum((s.cost_usd or 0) for s in spans)
    dur = _dur(root) if root else 0.0
    return (f"▸ {name} — {steps} steps · {_fmt_tokens(tokens)} tok · "
            f"{_short(cost)} · {dur:.1f}s")


def format_run_summary(spans: list[Span], insights: list[Insight] | None = None) -> str:
    insights = analyze(spans) if insights is None else insights
    body = format_insights(insights)
    return f"{run_header(spans)}\n{body}"
