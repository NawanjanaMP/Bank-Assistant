"""Shared guard rails for every MCP server.

Each tool call is checked here *again*, independently of the orchestrator:

1. a valid, short-lived user-context token must be present in request ``_meta``;
2. the customer must hold the tool's scope (from the shared policy);
3. write tools need an approval bound to the exact tool + arguments, and step-up
   tools need a stepped-up session;
4. every call is audit-logged with outcome and latency (no payloads).

The customer id used for core-banking calls comes only from the verified token.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from prometheus_client import Counter, Histogram

from bankai.core_banking.client import CoreBankingError
from bankai.shared import context_token
from bankai.shared.config import Settings
from bankai.shared.logging import get_logger
from bankai.shared.policy import policy_for

IDEMPOTENCY_META_KEY = "bank.ai/idempotency-key"

log = get_logger("bankai.mcp")
TOOL_CALLS = Counter("bankai_mcp_tool_calls_total", "MCP tool calls", ["server", "tool", "outcome"])
TOOL_LATENCY = Histogram("bankai_mcp_tool_latency_seconds", "MCP tool latency", ["server", "tool"])


def audience_for(server: str) -> str:
    return f"bankai-mcp-{server}"


class Guard:
    def __init__(self, server: str, settings: Settings) -> None:
        self.server = server
        self.settings = settings

    def authorize(
        self, ctx: Context, tool: str, arguments: dict[str, Any]
    ) -> tuple[context_token.UserContext, str | None]:
        meta = ctx.request_context.meta or {}
        token = meta.get(context_token.META_KEY)
        if not token:
            raise ToolError("unauthenticated: missing user context")
        try:
            user = context_token.verify(
                token,
                key=self.settings.context_token_signing_key.get_secret_value(),
                issuer=self.settings.context_token_issuer,
                audience=audience_for(self.server),
            )
        except context_token.ContextTokenError as exc:
            raise ToolError("unauthenticated: invalid user context") from exc

        policy = policy_for(tool)
        if policy is None:
            raise ToolError("forbidden: tool is not covered by policy")
        if not user.has_scope(policy.scope):
            raise ToolError(f"forbidden: missing scope {policy.scope}")
        idem = None
        if policy.write:
            if user.approved_action != context_token.action_fingerprint(tool, arguments):
                raise ToolError("forbidden: write action was not confirmed by the customer")
            if policy.step_up and not user.stepped_up:
                raise ToolError("forbidden: step-up authentication required")
            idem = meta.get(IDEMPOTENCY_META_KEY)
            if not idem or len(str(idem)) < 8:
                raise ToolError("bad request: idempotency key required for write actions")
        return user, idem

    async def run(
        self,
        ctx: Context,
        tool: str,
        arguments: dict[str, Any],
        fn: Callable[[context_token.UserContext, str | None], Awaitable[Any]],
    ) -> Any:
        started = time.perf_counter()
        outcome = "ok"
        user = None
        try:
            user, idem = self.authorize(ctx, tool, arguments)
            result = await fn(user, idem)
            return _jsonable(result)
        except ToolError:
            outcome = "denied"
            raise
        except CoreBankingError as exc:
            outcome = exc.code
            # Message is safe to show: it never contains other customers' data.
            raise ToolError(f"{exc.code}: {exc}") from exc
        except Exception as exc:  # pragma: no cover - defensive
            outcome = "error"
            log.exception("tool_failed", server=self.server, tool=tool)
            raise ToolError("internal error: the banking service is temporarily unavailable") from exc
        finally:
            elapsed = time.perf_counter() - started
            TOOL_CALLS.labels(self.server, tool, outcome).inc()
            TOOL_LATENCY.labels(self.server, tool).observe(elapsed)
            log.info(
                "audit.tool_call",
                server=self.server, tool=tool, outcome=outcome, latency_ms=round(elapsed * 1000, 1),
                subject=getattr(user, "subject", None), session_id=getattr(user, "session_id", None),
                request_id=getattr(user, "request_id", None),
            )


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    return value


def mask_account_number(number: str) -> str:
    return f"••••{number[-4:]}" if len(number) > 4 else number
