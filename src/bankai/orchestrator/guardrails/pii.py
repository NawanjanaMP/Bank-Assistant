"""PII Redaction: reversible tokenisation between the bank and any LLM.

Everything that crosses into a model prompt - customer text, history and tool results -
passes through ``redact``; anything coming back the other way (tool arguments chosen
by the model, final answers shown to the customer) passes through ``restore``.

* Tokens are stable within a session (the same card number is always ``[CARD_1]``), so
  the model can still reason about "that card" across turns.
* Originals live only in the session vault, encrypted at rest (Fernet).
* Detection = high-precision regexes with checksum validation + key-based redaction for
  structured tool output. An NER detector (e.g. Microsoft Presidio) can be plugged in via
  ``extra_detectors`` for names / free-text addresses.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Protocol


class Vault(Protocol):
    async def vault_put(self, sid: str, token: str, value: str) -> None: ...
    async def vault_all(self, sid: str) -> dict[str, str]: ...


@dataclass(frozen=True)
class Detector:
    kind: str
    pattern: re.Pattern[str]
    validate: Callable[[str], bool] = lambda _v: True


def _digits(v: str) -> str:
    return re.sub(r"\D", "", v)


def luhn_ok(value: str) -> bool:
    d = _digits(value)
    if not 13 <= len(d) <= 19:
        return False
    total = 0
    for i, ch in enumerate(reversed(d)):
        n = int(ch)
        if i % 2:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _sl_nic_ok(v: str) -> bool:
    # Old format: 9 digits + V/X; new format: 12 digits starting with birth year 19xx/20xx.
    return bool(re.fullmatch(r"\d{9}[VvXx]", v)) or (len(v) == 12 and v[:2] in ("19", "20"))


DEFAULT_DETECTORS: list[Detector] = [
    Detector("EMAIL", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    Detector("CARD", re.compile(r"\b(?:\d[ -]?){12,18}\d\b"), luhn_ok),
    Detector("IBAN", re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")),
    Detector("NIC", re.compile(r"\b(?:\d{9}[VvXx]|\d{12})\b"), _sl_nic_ok),
    Detector("PHONE", re.compile(r"(?:\+94|\b0)(?:[ -]?\d){9}\b")),
    Detector("ACCOUNT_NUMBER", re.compile(r"\b\d{10,16}\b")),
]

# Structured tool output: these keys are always tokenised regardless of their shape.
SENSITIVE_KEYS: dict[str, str] = {
    "account_number": "ACCOUNT_NUMBER",
    "email": "EMAIL",
    "mobile": "PHONE",
    "phone": "PHONE",
    "full_name": "NAME",
    "nic": "NIC",
    "line1": "ADDRESS",
    "line2": "ADDRESS",
}

TOKEN_RE = re.compile(r"\[(?:[A-Z_]+)_\d+\]")


class PiiRedactor:
    def __init__(self, vault: Vault, extra_detectors: Iterable[Detector] = ()) -> None:
        self.vault = vault
        self.detectors = [*DEFAULT_DETECTORS, *extra_detectors]

    async def _mapping(self, sid: str) -> tuple[dict[str, str], dict[str, str]]:
        tok_to_val = await self.vault.vault_all(sid)
        return tok_to_val, {v: k for k, v in tok_to_val.items()}

    async def _tokenise(self, sid: str, kind: str, value: str, cache: tuple[dict, dict]) -> str:
        tok_to_val, val_to_tok = cache
        if value in val_to_tok:
            return val_to_tok[value]
        n = 1 + sum(1 for t in tok_to_val if t.startswith(f"[{kind}_"))
        token = f"[{kind}_{n}]"
        await self.vault.vault_put(sid, token, value)
        tok_to_val[token] = value
        val_to_tok[value] = token
        return token

    async def redact_text(self, sid: str, text: str, *, cache: tuple[dict, dict] | None = None) -> tuple[str, int]:
        """Return (redacted_text, number_of_entities_replaced)."""
        cache = cache or await self._mapping(sid)
        count = 0
        for det in self.detectors:
            out: list[str] = []
            pos = 0
            for m in det.pattern.finditer(text):
                raw = m.group(0)
                if TOKEN_RE.fullmatch(raw) or not det.validate(raw):
                    continue
                out.append(text[pos:m.start()])
                out.append(await self._tokenise(sid, det.kind, raw.strip(), cache))
                pos = m.end()
                count += 1
            out.append(text[pos:])
            text = "".join(out)
        return text, count

    async def redact_obj(self, sid: str, obj: Any, *, cache: tuple[dict, dict] | None = None) -> tuple[Any, int]:
        cache = cache or await self._mapping(sid)
        if isinstance(obj, dict):
            total = 0
            out = {}
            for k, v in obj.items():
                if k in SENSITIVE_KEYS and isinstance(v, str) and v and not v.startswith("••••"):
                    out[k] = await self._tokenise(sid, SENSITIVE_KEYS[k], v, cache)
                    total += 1
                else:
                    out[k], n = await self.redact_obj(sid, v, cache=cache)
                    total += n
            return out, total
        if isinstance(obj, list):
            items, total = [], 0
            for v in obj:
                r, n = await self.redact_obj(sid, v, cache=cache)
                items.append(r)
                total += n
            return items, total
        if isinstance(obj, str):
            return await self.redact_text(sid, obj, cache=cache)
        return obj, 0

    async def restore_text(self, sid: str, text: str, mapping: dict[str, str] | None = None) -> str:
        mapping = mapping if mapping is not None else await self.vault.vault_all(sid)
        return TOKEN_RE.sub(lambda m: mapping.get(m.group(0), m.group(0)), text)

    async def restore_obj(self, sid: str, obj: Any, mapping: dict[str, str] | None = None) -> Any:
        mapping = mapping if mapping is not None else await self.vault.vault_all(sid)
        if isinstance(obj, dict):
            return {k: await self.restore_obj(sid, v, mapping) for k, v in obj.items()}
        if isinstance(obj, list):
            return [await self.restore_obj(sid, v, mapping) for v in obj]
        if isinstance(obj, str):
            return await self.restore_text(sid, obj, mapping)
        return obj


def contains_raw_pii(text: str) -> list[str]:
    """Used by output checks and the eval suite: which PII kinds are present in clear text."""
    found = []
    for det in DEFAULT_DETECTORS:
        for m in det.pattern.finditer(text):
            if det.validate(m.group(0)):
                found.append(det.kind)
                break
    return found
