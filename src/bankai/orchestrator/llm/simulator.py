"""Deterministic offline LLM ("simulator" provider).

Used by the test-suite, the CI evaluation harness and keyless local demos. It follows the
same tool-calling protocol as a real model, so every other component (routing, MCP,
authorisation, PII, confirmations, streaming, cost) is exercised for real. It is NOT a
quality proxy: run ``bankai-eval --provider anthropic`` for model quality.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import AsyncIterator
from datetime import date, timedelta
from typing import Any

from bankai.orchestrator.llm.base import LLMRequest, LLMResponse, StreamEvent, Usage

_INTENTS = {
    "accounts": re.compile(r"\b(balance|how much (money|do i have)|my accounts?|available funds)\b", re.I),
    "transactions": re.compile(r"\b(transactions?|statement|spent|spending|payments?|charges?|T-\d+)\b", re.I),
    "service": re.compile(r"\b(address|moved|cheque ?book|check ?book|kyc|occupation|employer|profile)\b", re.I),
}


def _tid() -> str:
    return f"toolu_sim_{uuid.uuid4().hex[:10]}"


def _last_user_text(messages: list[dict[str, Any]]) -> str:
    for m in reversed(messages):
        if m["role"] != "user":
            continue
        c = m["content"]
        if isinstance(c, str):
            return c
        texts = [b["text"] for b in c if b.get("type") == "text"]
        if texts:
            return "\n".join(texts)
    return ""


def _tool_results(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Map tool name -> parsed result for every tool called so far in this agent run."""
    names: dict[str, str] = {}
    out: dict[str, Any] = {}
    for m in messages:
        if not isinstance(m["content"], list):
            continue
        for b in m["content"]:
            if b.get("type") == "tool_use":
                names[b["id"]] = b["name"]
            elif b.get("type") == "tool_result":
                raw = b["content"] if isinstance(b["content"], str) else json.dumps(b["content"])
                body = re.sub(r"^<tool_output[^>]*>\n?|\n?</tool_output>$", "", raw.strip())
                try:
                    val: Any = json.loads(body)
                except json.JSONDecodeError:
                    val = body
                out.setdefault(names.get(b["tool_use_id"], "?"), []).append(val)
    return out


def _fmt_money(v: Any) -> str:
    try:
        return f"LKR {float(v):,.2f}"
    except (TypeError, ValueError):
        return str(v)


class SimulatorLLM:
    provider = "simulator"

    async def complete(self, req: LLMRequest, model: str = "simulator") -> LLMResponse:
        blocks = self._decide(req)
        text_len = sum(len(b.get("text", "")) for b in blocks)
        return LLMResponse(
            content=blocks,
            stop_reason="tool_use" if any(b["type"] == "tool_use" for b in blocks) else "end_turn",
            usage=Usage(input_tokens=len(req.system) // 4 + 200, output_tokens=max(20, text_len // 4)),
            model="simulator", provider="simulator", latency_ms=5.0,
        )

    async def stream(self, req: LLMRequest, model: str = "simulator") -> AsyncIterator[StreamEvent]:
        resp = await self.complete(req, model)
        for word in re.findall(r"\S+\s*", resp.text):
            yield StreamEvent("text", text=word)
        yield StreamEvent("final", response=resp)

    # ------------------------------------------------------------------ policy
    def _decide(self, req: LLMRequest) -> list[dict[str, Any]]:
        tools = {t.name for t in req.tools}
        if any(n.startswith("delegate_to_") for n in tools):
            return self._coordinator(req)
        return self._specialist(req, tools)

    def _coordinator(self, req: LLMRequest) -> list[dict[str, Any]]:
        text = _last_user_text(req.messages)
        hits = [a for a, p in _INTENTS.items() if p.search(text)]
        if hits:
            return [{"type": "tool_use", "id": _tid(), "name": f"delegate_to_{a}", "input": {"request": text}}
                    for a in hits]
        if re.search(r"\b(hi|hello|hey|good (morning|afternoon|evening))\b", text, re.I):
            return [{"type": "text", "text": "Hello! I can help with balances, transactions and statements, "
                                             "or service requests like a change of address, a cheque book or "
                                             "a KYC update. What would you like to do?"}]
        return [{"type": "text", "text": "I can help with your accounts, transactions and service requests. "
                                         "Could you tell me a bit more about what you need?"}]

    def _specialist(self, req: LLMRequest, tools: set[str]) -> list[dict[str, Any]]:
        task = _last_user_text(req.messages)
        done = _tool_results(req.messages)
        call = lambda name, **inp: [{"type": "tool_use", "id": _tid(), "name": name, "input": inp}]  # noqa: E731
        say = lambda t: [{"type": "text", "text": t}]  # noqa: E731

        if any(isinstance(v, str) and v.startswith("Error") for vs in done.values() for v in vs):
            err = next(v for vs in done.values() for v in vs if isinstance(v, str) and v.startswith("Error"))
            return say(f"I wasn't able to complete that: {err.removeprefix('Error: ')}")

        for vs in done.values():
            for v in vs:
                if isinstance(v, dict) and v.get("status") == "awaiting_confirmation":
                    return say(f"I've prepared this request: {v.get('summary')}. Please review and confirm it "
                               "in the card below - nothing has been submitted yet.")

        accounts = (done.get("list_accounts") or [[]])[0]

        # --- Accounts agent
        if "get_balance" in tools:
            if not accounts and "list_accounts" not in done:
                return call("list_accounts")
            if "get_balance" not in done:
                return [b for a in accounts for b in call("get_balance", account_id=a["account_id"])]
            parts = []
            for bal in done["get_balance"]:
                acc: dict[str, Any] = next((a for a in accounts if a["account_id"] == bal.get("account_id")), {})
                label = acc.get("nickname") or acc.get("type", "account")
                parts.append(f"{_fmt_money(bal.get('available'))} available in {label} "
                             f"({acc.get('account_number', bal.get('account_id'))})")
            return say("You have " + " and ".join(parts) + ".")

        # --- Transactions agent
        if "get_transaction_details" in tools:
            m = re.search(r"\bT-\d+\b", task)
            if m:
                if "get_transaction_details" not in done:
                    return call("get_transaction_details", transaction_id=m.group(0))
                t = done["get_transaction_details"][0]
                return say(f"Transaction {t['transaction_id']} on {t['booked_at'][:10]}: "
                           f"{_fmt_money(t['amount'])} - {t['description']} ({t.get('channel')}).")
            if not accounts and "list_accounts" not in done:
                return call("list_accounts")
            target = accounts[0]["account_id"] if accounts else "A-1"
            if re.search(r"statement", task, re.I):
                first = date.today().replace(day=1)
                last_month_end = first - timedelta(days=1)
                return call("request_statement", account_id=target, from_date=str(last_month_end.replace(day=1)),
                            to_date=str(last_month_end), delivery="email")
            if "get_recent_transactions" not in done:
                return call("get_recent_transactions", account_id=target, limit=5)
            rows = done["get_recent_transactions"][0]
            spent = -sum(float(t["amount"]) for t in rows if float(t["amount"]) < 0)
            label = accounts[0].get("nickname") or "account" if accounts else "account"
            return say(f"Here are the last {len(rows)} transactions on your {label}. Money out across them "
                       f"comes to {_fmt_money(spent)}.")

        # --- Service agent
        if re.search(r"cheque ?book|check ?book", task, re.I):
            if not accounts and "list_accounts" not in done:
                return call("list_accounts")
            current = next((a for a in accounts if a["type"] == "current"), None)
            if not current:
                return say("Cheque books are only available on current accounts, and I couldn't find one.")
            return call("request_cheque_book", account_id=current["account_id"], leaves=25, delivery="branch")
        if re.search(r"address|moved", task, re.I):
            m = re.search(r"\bto\s+(.+)$", task, re.I | re.S)
            parts = [p.strip() for p in (m.group(1) if m else "").split(",") if p.strip()]
            if len(parts) < 3:
                return say("Sure - what's your new address? Please include the street, city and postal code.")
            return call("change_address", line1=parts[0], city=parts[1], postal_code=parts[2],
                        country=(parts[3] if len(parts) > 3 and len(parts[3]) == 2 else "LK"))
        if re.search(r"kyc|occupation|employer", task, re.I):
            occ = re.search(r"occupation (?:is|:)\s*([\w ]+?)(?:[,.]|$)", task, re.I)
            if occ:
                return call("update_kyc", occupation=occ.group(1).strip(), source_of_funds="salary")
            if "get_kyc_status" not in done:
                return call("get_kyc_status")
            k = done["get_kyc_status"][0]
            missing = ", ".join(k.get("missing_items") or []) or "nothing"
            return say(f"Your KYC status is '{k['status']}', next review due {k['next_review_due']}. "
                       f"Missing information: {missing}.")
        if "get_profile" in tools and "get_profile" not in done:
            return call("get_profile")
        return say("I can help with a change of address, a cheque book or a KYC update. Which would you like?")
