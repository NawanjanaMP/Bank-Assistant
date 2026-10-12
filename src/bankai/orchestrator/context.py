"""Per-turn context passed through the coordinator, agents and the MCP gateway."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from bankai.orchestrator.guardrails.pii import PiiRedactor
from bankai.orchestrator.security.oidc import Principal
from bankai.orchestrator.session.store import SessionStore

Emit = Callable[[str, dict[str, Any]], Awaitable[None]]


async def _noop(_event: str, _data: dict[str, Any]) -> None:
    return None


@dataclass
class TurnContext:
    principal: Principal
    session_id: str
    request_id: str
    store: SessionStore
    redactor: PiiRedactor
    emit: Emit = _noop
    # Collected during the turn and persisted to history at the end (already tokenised).
    agent_outputs: list[dict[str, str]] = field(default_factory=list)
    pending_actions: list[str] = field(default_factory=list)
