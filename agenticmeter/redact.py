"""Redaction runs on every span's input/output before it is stored or exported.

Default is ON: scrub common secret patterns + truncate oversized values.
Configure via agenticmeter.configure(redact=...) with one of:
  "scrub"   - mask secrets, then truncate (default)
  "truncate"- truncate only, keep content
  "off"     - raw capture (local debugging)
or a callable(value) -> value for full control.
"""
from __future__ import annotations

import re
from typing import Any, Callable

MAX_LEN = 4000  # characters per captured value before truncation

# Conservative, high-precision patterns — better to miss than to mangle real text.
_PATTERNS = [
    (re.compile(r"sk-[A-Za-z0-9]{16,}"), "<openai_key>"),
    (re.compile(r"sk-ant-[A-Za-z0-9\-_]{16,}"), "<anthropic_key>"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"), "<github_token>"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "<aws_key>"),
    (re.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"), "<jwt>"),
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "<email>"),
    (re.compile(r"\b(?:\d[ -]?){13,19}\b"), "<card>"),
]


def _scrub_text(s: str) -> str:
    for pat, repl in _PATTERNS:
        s = pat.sub(repl, s)
    return s


def _walk(value: Any, scrub: bool) -> Any:
    if isinstance(value, str):
        if scrub:
            value = _scrub_text(value)
        if len(value) > MAX_LEN:
            value = value[:MAX_LEN] + f"...<truncated {len(value) - MAX_LEN} chars>"
        return value
    if isinstance(value, dict):
        return {k: _walk(v, scrub) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_walk(v, scrub) for v in value][:200]  # cap fan-out too
    return value


def make_redactor(mode: str | Callable) -> Callable[[Any], Any]:
    if callable(mode):
        return mode
    if mode == "off":
        return lambda v: v
    if mode == "truncate":
        return lambda v: _walk(v, scrub=False)
    # default "scrub"
    return lambda v: _walk(v, scrub=True)
