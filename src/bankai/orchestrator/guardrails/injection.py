"""Prompt-injection and input hygiene.

Two threat paths:

* **Direct** - the customer tries to override the assistant's rules. We score the input
  with cheap heuristics; high scores are refused before any model call, medium scores are
  flagged in telemetry and the agent is reminded of its rules.
* **Indirect** - text controlled by third parties reaches the model inside tool results
  (merchant narratives, payee names). Tool results are always wrapped in an explicit
  untrusted-data envelope and suspicious spans are neutralised.

Heuristics are a first layer, not the defence. The real defence is architectural: the
model has no identity to steal (customer id is injected server-side), cannot reach tools
outside its agent's allow-list, and cannot execute a write without a customer click.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass

_PATTERNS: list[tuple[re.Pattern[str], float]] = [
    (re.compile(r"ignore (all |any )?(previous|prior|above) (instructions|rules|prompts?)", re.I), 0.9),
    (re.compile(r"disregard (the |your )?(system|previous) (prompt|instructions)", re.I), 0.9),
    (re.compile(r"(reveal|print|show|repeat) (me )?(your|the) (system prompt|instructions|hidden)", re.I), 0.8),
    (re.compile(r"you are now (DAN|developer mode|an? unrestricted)", re.I), 0.9),
    (re.compile(r"\b(jailbreak|do anything now)\b", re.I), 0.6),
    (re.compile(r"(act|pretend) as (the )?(bank )?(admin|administrator|system|root)", re.I), 0.7),
    (re.compile(r"(other|another|all) customers?'?s? (accounts?|data|balances?)", re.I), 0.6),
    (re.compile(r"customer[_ ]?id\s*[:=]", re.I), 0.5),
    (re.compile(r"</?(system|tool_result|untrusted)[^>]*>", re.I), 0.7),
]

_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿"), None)


@dataclass(frozen=True)
class InjectionVerdict:
    score: float
    reasons: tuple[str, ...]

    @property
    def block(self) -> bool:
        return self.score >= 0.85

    @property
    def suspicious(self) -> bool:
        return self.score >= 0.5


def normalise_input(text: str, max_chars: int) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_ZERO_WIDTH)
    text = "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch)[0] != "C")
    return text.strip()[:max_chars]


def score(text: str) -> InjectionVerdict:
    hits = [(p.pattern, w) for p, w in _PATTERNS if p.search(text)]
    if not hits:
        return InjectionVerdict(0.0, ())
    top = max(w for _, w in hits)
    combined = min(1.0, top + 0.1 * (len(hits) - 1))
    return InjectionVerdict(combined, tuple(p for p, _ in hits))


def wrap_untrusted_tool_output(tool: str, payload: object) -> str:
    """Serialize a tool result inside an envelope the system prompt tells agents to treat as data."""
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, default=str)
    flagged = False
    for p, w in _PATTERNS:
        if w >= 0.6 and p.search(text):
            text = p.sub("[removed: instruction-like text]", text)
            flagged = True
    note = ' note="instruction-like text from a third party was removed"' if flagged else ""
    return f'<tool_output tool="{tool}" trust="untrusted-data"{note}>\n{text}\n</tool_output>'
