"""MCP gateway: the only path from an agent to a bank system.

For every tool call the gateway:

1. checks the agent allow-list and the customer's scopes (Authorisation);
2. restores PII tokens in the model-chosen arguments (``[ACCOUNT_NUMBER_1]`` -> real value);
3. for **write** tools, does *not* execute - it parks a pending action and asks the customer
   to confirm (human-in-the-loop). Execution happens later via ``execute_approved``;
4. mints a short-lived, audience-bound user-context token and calls the MCP server;
5. tokenises PII in the result and wraps it as untrusted data before the model sees it.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

from mcp import Client
from mcp.server.mcpserver import MCPServer
from opentelemetry import trace

from bankai.mcp_servers.common import IDEMPOTENCY_META_KEY, audience_for
from bankai.orchestrator.context import TurnContext
from bankai.orchestrator.guardrails.injection import wrap_untrusted_tool_output
from bankai.orchestrator.llm.base import ToolSpec
from bankai.orchestrator.observability import metrics
from bankai.orchestrator.security import authz
from bankai.shared import context_token
from bankai.shared.config import Settings
from bankai.shared.logging import get_logger
from bankai.shared.policy import policy_for

log = get_logger("bankai.gateway")
tracer = trace.get_tracer("bankai.gateway")

type ServerTarget = str | MCPServer  # URL in deployments, in-process server in tests


@dataclass
class ToolOutcome:
    """What goes back to the model (``model_content``) and what goes to the UI (``display``)."""

    model_content: str
    is_error: bool
    display: Any = None
    status: str = "ok"


class MCPGateway:
    def __init__(self, servers: dict[str, ServerTarget], settings: Settings, ledger: Any = None) -> None:
        self.servers = servers
        self.s = settings
        self.ledger = ledger
        self._tool_cache: dict[str, tuple[float, list[ToolSpec]]] = {}
        self._tool_server: dict[str, str] = {}
        self._defaults: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------- connections
    def _client(self, server: str) -> Client:
        return Client(self.servers[server], read_timeout_seconds=self.s.mcp_timeout_seconds)

    async def list_tools(self, server: str, ttl: float = 300.0) -> list[ToolSpec]:
        cached = self._tool_cache.get(server)
        if cached and time.monotonic() - cached[0] < ttl:
            return cached[1]
        async with self._client(server) as c:
            result = await c.list_tools()
        specs = [ToolSpec(t.name, t.description or "", t.input_schema) for t in result.tools]
        for t in specs:
            self._tool_server[t.name] = server
            # Schema defaults are applied explicitly so the orchestrator and the MCP server
            # fingerprint exactly the same argument set for confirmed write actions.
            self._defaults[t.name] = {
                k: p["default"] for k, p in (t.input_schema.get("properties") or {}).items() if "default" in p
            }
        self._tool_cache[server] = (time.monotonic(), specs)
        return specs

    def _token(self, ctx: TurnContext, server: str, approved_action: str | None = None,
               stepped_up: bool = False) -> str:
        user = context_token.UserContext(
            subject=ctx.principal.subject, customer_id=ctx.principal.customer_id, scopes=ctx.principal.scopes,
            stepped_up=stepped_up, session_id=ctx.session_id, request_id=ctx.request_id,
            approved_action=approved_action,
        )
        return context_token.mint(
            user, key=self.s.context_token_signing_key.get_secret_value(), issuer=self.s.context_token_issuer,
            audience=audience_for(server), ttl=self.s.context_token_ttl_seconds,
        )

    async def _invoke(self, server: str, tool: str, args: dict[str, Any], meta: dict[str, Any]) -> tuple[bool, Any]:
        try:
            async with self._client(server) as c:
                result = await c.call_tool(tool, args, meta=meta)
        except Exception as exc:  # transport failure: surface a safe, retryable message to the agent
            log.warning("mcp_unavailable", server=server, tool=tool, error=type(exc).__name__)
            return False, "service temporarily unavailable, please try again shortly"
        texts = [getattr(b, "text", "") for b in result.content]
        if result.is_error:
            return False, "".join(texts)
        structured = result.structured_content
        if isinstance(structured, dict) and set(structured) == {"result"}:
            return True, structured["result"]  # list/scalar returns are wrapped by the SDK
        if structured is not None:
            return True, structured
        try:
            parsed = [json.loads(t) for t in texts]
        except json.JSONDecodeError:
            return True, "".join(texts)
        return True, parsed[0] if len(parsed) == 1 else parsed

    # ------------------------------------------------------------- agent path
    async def call(self, ctx: TurnContext, agent: str, allowed: frozenset[str], tool: str,
                   model_args: dict[str, Any]) -> ToolOutcome:
        started = time.perf_counter()
        with tracer.start_as_current_span("mcp.tool") as span:
            span.set_attributes({"tool": tool, "agent": agent})
            decision = authz.can_call(ctx.principal, agent, tool, allowed)
            if not decision:
                metrics.TOOL_CALLS.labels(agent, tool, "denied").inc()
                metrics.GUARDRAIL_EVENTS.labels("tool_denied").inc()
                await self._audit(ctx, "tool.denied", tool, "denied", reason=decision.reason)
                return ToolOutcome(f"Error: forbidden - {decision.reason}", True, status="denied")

            server = self._tool_server.get(tool)
            if server is None:
                return ToolOutcome(f"Error: unknown tool {tool}", True, status="error")
            restored = await ctx.redactor.restore_obj(ctx.session_id, model_args or {})
            args = {**self._defaults.get(tool, {}), **restored}
            policy = policy_for(tool)
            if policy is None:  # unreachable: can_call already denied unknown tools
                return ToolOutcome("Error: forbidden", True, status="denied")

            if policy.write:
                return await self._park_for_confirmation(ctx, agent, server, tool, args)

            ok, payload = await self._invoke(
                server, tool, args, {context_token.META_KEY: self._token(ctx, server)})
            elapsed = time.perf_counter() - started
            metrics.TOOL_CALLS.labels(agent, tool, "ok" if ok else "error").inc()
            metrics.TOOL_LATENCY.labels(tool).observe(elapsed)
            if not ok:
                return ToolOutcome(f"Error: {payload}", True, status="error")
            redacted, n = await ctx.redactor.redact_obj(ctx.session_id, payload)
            if n:
                metrics.PII_ENTITIES.labels("tool_result").inc(n)
            return ToolOutcome(wrap_untrusted_tool_output(tool, redacted), False, display=payload)

    async def _park_for_confirmation(self, ctx: TurnContext, agent: str, server: str, tool: str,
                                     args: dict[str, Any]) -> ToolOutcome:
        policy = policy_for(tool)
        if policy is None:
            raise ValueError(f"no policy for {tool}")
        action_id = uuid.uuid4().hex
        try:
            fields = {k: v for k, v in args.items() if v is not None}
            fields.setdefault("field_summary", ", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in fields.items()))
            summary = policy.summary.format_map(_Default(fields))
        except (KeyError, ValueError):
            summary = f"{tool.replace('_', ' ')}"
        expires_at = int(time.time()) + 600
        await ctx.store.put_action(ctx.session_id, action_id, {
            "action_id": action_id, "tool": tool, "server": server, "agent": agent, "args": args,
            "summary": summary, "step_up": policy.step_up, "request_id": ctx.request_id,
            "created_at": int(time.time()), "expires_at": expires_at,
        })
        ctx.pending_actions.append(action_id)
        metrics.CONFIRMATIONS.labels(tool, "requested").inc()
        await ctx.emit("confirmation.required", {
            "action_id": action_id, "tool": tool, "agent": agent, "title": tool.replace("_", " ").capitalize(),
            "summary": summary, "step_up": policy.step_up, "expires_at": expires_at,
        })
        await self._audit(ctx, "action.requested", tool, "pending", action_id=action_id)
        redacted_summary, _ = await ctx.redactor.redact_text(ctx.session_id, summary)
        return ToolOutcome(
            wrap_untrusted_tool_output(tool, {
                "status": "awaiting_confirmation", "action_id": action_id, "summary": redacted_summary,
                "instruction": "Nothing has been executed. Ask the customer to review and confirm in the app.",
            }),
            False, status="awaiting_confirmation",
        )

    # ------------------------------------------------------------- confirmation path
    async def execute_approved(self, ctx: TurnContext, action: dict[str, Any], stepped_up: bool) -> tuple[bool, Any]:
        """Run a write action the customer just confirmed. Never reachable from a model."""
        tool, server, args = action["tool"], action["server"], action["args"]
        token = self._token(ctx, server, approved_action=context_token.action_fingerprint(tool, args),
                            stepped_up=stepped_up)
        ok, payload = await self._invoke(server, tool, args, {
            context_token.META_KEY: token, IDEMPOTENCY_META_KEY: action["action_id"]})
        metrics.CONFIRMATIONS.labels(tool, "executed" if ok else "failed").inc()
        await self._audit(ctx, "action.executed", tool, "ok" if ok else "error", action_id=action["action_id"])
        return ok, payload

    async def _audit(self, ctx: TurnContext, event: str, tool: str, outcome: str, **details: Any) -> None:
        log.info(f"audit.{event}", tool=tool, outcome=outcome, subject=ctx.principal.subject,
                 session_id=ctx.session_id, request_id=ctx.request_id, **details)
        if self.ledger is not None:
            await self.ledger.audit(event, subject=ctx.principal.subject, outcome=outcome,
                                    session_id=ctx.session_id, request_id=ctx.request_id, tool=tool, **details)


class _Default(dict):
    def __missing__(self, key: str) -> str:
        return "-"
