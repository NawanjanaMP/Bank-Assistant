"""Authorisation: what this customer may ask which agent to do, and which tools it may run.

Decisions are made in code from the shared policy, never by a model. The coordinator asks
``can_delegate`` before handing work to a specialist; the MCP gateway asks ``can_call``
before every tool call (and the MCP server re-checks with its own copy of the policy).
"""

from __future__ import annotations

from dataclasses import dataclass

from bankai.orchestrator.security.oidc import Principal
from bankai.shared.policy import AGENT_REQUIRED_SCOPES, AgentName, policy_for


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str = ""

    def __bool__(self) -> bool:
        return self.allowed


ALLOW = Decision(True)


def can_delegate(principal: Principal, agent: AgentName) -> Decision:
    missing = {s for s in AGENT_REQUIRED_SCOPES.get(agent, set()) if s not in principal.scopes}
    if missing:
        return Decision(False, f"customer lacks scope(s): {', '.join(sorted(missing))}")
    return ALLOW


def can_call(principal: Principal, agent: str, tool: str, allowed_tools: frozenset[str]) -> Decision:
    if tool not in allowed_tools:
        return Decision(False, f"tool '{tool}' is not available to the {agent} agent")
    policy = policy_for(tool)
    if policy is None:
        return Decision(False, f"tool '{tool}' has no policy (deny by default)")
    if policy.scope not in principal.scopes:
        return Decision(False, f"customer lacks scope {policy.scope}")
    return ALLOW
