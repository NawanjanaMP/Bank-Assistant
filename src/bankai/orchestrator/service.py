"""Turn service: one customer message in, a stream of events out.

Order of operations for a turn:

    budget check -> input hygiene -> injection screen -> PII tokenisation -> coordinator
    (-> authorisation -> specialists -> MCP gateway) -> persist tokenised history -> usage

Confirmation of a parked write action is a separate, model-free path.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from bankai.orchestrator.agents.coordinator import Coordinator
from bankai.orchestrator.context import Emit, TurnContext
from bankai.orchestrator.guardrails import injection
from bankai.orchestrator.guardrails.pii import PiiRedactor
from bankai.orchestrator.llm.router import NoProviderAvailable
from bankai.orchestrator.mcp.gateway import MCPGateway
from bankai.orchestrator.observability import metrics
from bankai.orchestrator.observability.cost import BudgetExceeded, CostTracker
from bankai.orchestrator.security.oidc import Principal
from bankai.orchestrator.session.store import SessionStore
from bankai.shared.config import Settings
from bankai.shared.logging import get_logger

log = get_logger("bankai.turn")

BLOCKED_REPLY = ("I can only help with your own banking - balances, transactions, statements and service "
                 "requests. I can't change how I work or share information about other customers.")


class ActionError(Exception):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


class TurnService:
    def __init__(self, settings: Settings, store: SessionStore, redactor: PiiRedactor, coordinator: Coordinator,
                 gateway: MCPGateway, cost: CostTracker, ledger: Any = None) -> None:
        self.s = settings
        self.store = store
        self.redactor = redactor
        self.coordinator = coordinator
        self.gateway = gateway
        self.cost = cost
        self.ledger = ledger

    def _ctx(self, principal: Principal, sid: str, emit: Emit) -> TurnContext:
        return TurnContext(principal=principal, session_id=sid, request_id=uuid.uuid4().hex,
                           store=self.store, redactor=self.redactor, emit=emit)

    async def handle_turn(self, principal: Principal, sid: str, raw_text: str, emit: Emit) -> None:
        started = time.perf_counter()
        ctx = self._ctx(principal, sid, emit)
        outcome = "ok"
        first_token: list[float] = []

        async def timed_emit(event: str, data: dict[str, Any]) -> None:
            if event == "message.delta" and not first_token:
                first_token.append(time.perf_counter() - started)
                metrics.TIME_TO_FIRST_TOKEN.observe(first_token[0])
            await emit(event, data)

        ctx.emit = timed_emit
        await emit("turn.started", {"session_id": sid, "request_id": ctx.request_id})
        try:
            async with self.store.turn_lock(sid):
                await self.cost.check_budget(sid, principal.subject)
                text = injection.normalise_input(raw_text, self.s.max_input_chars)
                if not text:
                    raise ActionError("empty_message", "Message is empty")

                verdict = injection.score(text)
                if verdict.suspicious:
                    kind = "blocked" if verdict.block else "flagged"
                    metrics.GUARDRAIL_EVENTS.labels(f"prompt_injection_{kind}").inc()
                    log.warning("prompt_injection", score=verdict.score, blocked=verdict.block, session_id=sid)
                if verdict.block:
                    outcome = "blocked"
                    await emit("guardrail", {"type": "prompt_injection", "message": "Request blocked by policy"})
                    await emit("message.delta", {"agent": "coordinator", "text": BLOCKED_REPLY})
                    ctx.agent_outputs.append({"agent": "coordinator", "text": BLOCKED_REPLY})
                    redacted_user, _ = await self.redactor.redact_text(sid, text)
                else:
                    redacted_user, n = await self.redactor.redact_text(sid, text)
                    if n:
                        metrics.PII_ENTITIES.labels("user_input").inc(n)
                        await emit("guardrail", {"type": "pii_redacted", "count": n})
                    history = [{"role": m["role"], "content": m["content"]} for m in await self.store.history(sid)]
                    await self.coordinator.run(ctx, history, redacted_user)

                answer = "\n\n".join(o["text"] for o in ctx.agent_outputs if o["text"].strip())
                await self.store.append(
                    sid,
                    {"role": "user", "content": redacted_user, "ts": time.time()},
                    {"role": "assistant", "content": answer or "(no answer)", "ts": time.time(),
                     "agents": [o["agent"] for o in ctx.agent_outputs], "actions": ctx.pending_actions},
                )
                meta = await self.store.get_meta(sid, principal.subject)
                title = meta.get("title", "")
                if title == "New conversation":
                    title = (await self.redactor.restore_text(sid, redacted_user))[:60]
                await self.store.touch(sid, principal.subject, title=title)
        except BudgetExceeded:
            outcome = "budget"
            await emit("error", {"code": "budget_exceeded",
                                 "message": "You've reached today's assistant usage limit. Please try again tomorrow."})
        except NoProviderAvailable:
            outcome = "llm_unavailable"
            await emit("error", {"code": "assistant_unavailable",
                                 "message": "The assistant is temporarily unavailable. Please try again shortly."})
        except ActionError as exc:
            outcome = exc.code
            await emit("error", {"code": exc.code, "message": str(exc)})
        except Exception:
            outcome = "error"
            log.exception("turn_failed", session_id=sid, request_id=ctx.request_id)
            await emit("error", {"code": "internal_error", "message": "Something went wrong. Please try again.",
                                 "request_id": ctx.request_id})
        finally:
            elapsed = time.perf_counter() - started
            metrics.TURNS.labels(outcome).inc()
            metrics.TURN_LATENCY.observe(elapsed)
            usage = await self.cost.session_usage(sid)
            await emit("turn.completed", {"request_id": ctx.request_id, "outcome": outcome,
                                          "latency_ms": round(elapsed * 1000), "session_usage": usage})

    # ------------------------------------------------------------------ confirmations
    async def confirm(self, principal: Principal, sid: str, action_id: str) -> dict[str, Any]:
        await self.store.get_meta(sid, principal.subject)  # ownership
        peek = await self.store.get_action(sid, action_id)
        if not peek:
            raise ActionError("action_not_found", "This request has expired or was already handled.", 404)
        stepped_up = principal.is_stepped_up(self.s)
        if peek["step_up"] and not stepped_up:
            # Leave the action in place so the customer can confirm after re-authenticating.
            raise ActionError("step_up_required", "Please verify it's you to continue.", 401)
        action = await self.store.take_action(sid, action_id)  # atomic: executes at most once
        if not action:
            raise ActionError("action_not_found", "This request has expired or was already handled.", 404)
        ctx = self._ctx(principal, sid, _discard)
        ok, payload = await self.gateway.execute_approved(ctx, action, stepped_up)
        if ok:
            ref = payload.get("request_id") if isinstance(payload, dict) else None
            note = f"Confirmed and submitted: {action['summary']}" + (f" (reference {ref})." if ref else ".")
        else:
            note = f"The request could not be completed: {payload}"
        redacted, _ = await self.redactor.redact_text(sid, note)
        await self.store.append(sid, {"role": "user", "content": f"[customer confirmed action {action_id}]"},
                                {"role": "assistant", "content": redacted, "agents": [action["agent"]]})
        return {"ok": ok, "message": note, "receipt": payload if ok else None}

    async def decline(self, principal: Principal, sid: str, action_id: str) -> dict[str, Any]:
        await self.store.get_meta(sid, principal.subject)
        action = await self.store.take_action(sid, action_id)
        if not action:
            raise ActionError("action_not_found", "This request has expired or was already handled.", 404)
        metrics.CONFIRMATIONS.labels(action["tool"], "declined").inc()
        if self.ledger is not None:
            await self.ledger.audit("action.declined", subject=principal.subject, outcome="declined",
                                    session_id=sid, tool=action["tool"], action_id=action_id)
        await self.store.append(sid, {"role": "user", "content": f"[customer cancelled action {action_id}]"},
                                {"role": "assistant", "agents": [action["agent"]],
                                 "content": "Okay, I've cancelled that request. Nothing was changed."})
        return {"ok": True, "message": "Cancelled. Nothing was changed."}


async def _discard(_e: str, _d: dict[str, Any]) -> None:
    return None
