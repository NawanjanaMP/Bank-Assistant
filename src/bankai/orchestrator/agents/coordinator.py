"""Coordinator agent: understands the request, checks Authorisation, delegates to specialists.

Specialists answer the customer directly (their text streams straight to the UI, labelled
with the agent), so a single-intent turn costs one cheap routing call plus the specialist's
own calls - no extra "summarise the specialist" round trip. Multi-intent requests run the
specialists one after another, each as its own answer segment.
"""

from __future__ import annotations

from typing import Any

from bankai.orchestrator.agents.base import SpecialistAgent, StreamRestorer, system_prompt
from bankai.orchestrator.agents.specialists import ALL as SPECS
from bankai.orchestrator.context import TurnContext
from bankai.orchestrator.llm.base import LLMRequest, LLMResponse, Message, ToolSpec
from bankai.orchestrator.llm.router import LLMRouter
from bankai.orchestrator.mcp.gateway import MCPGateway
from bankai.orchestrator.observability import metrics
from bankai.orchestrator.security.authz import can_delegate
from bankai.shared.config import Settings
from bankai.shared.policy import AgentName

SPECIALIST_HISTORY = 6


def delegate_tools() -> list[ToolSpec]:
    return [
        ToolSpec(
            name=f"delegate_to_{spec.name.value}",
            description=f"Hand this request to the {spec.title}: {spec.description}",
            input_schema={
                "type": "object",
                "properties": {"request": {
                    "type": "string",
                    "description": "Self-contained description of what the customer wants from this specialist.",
                }},
                "required": ["request"],
            },
        )
        for spec in SPECS.values()
    ]


class Coordinator:
    def __init__(self, router: LLMRouter, gateway: MCPGateway, settings: Settings) -> None:
        self.router = router
        self.s = settings
        self.specialists = {name: SpecialistAgent(spec, router, gateway, settings) for name, spec in SPECS.items()}
        self.tools = delegate_tools()

    async def run(self, ctx: TurnContext, history: list[Message], user_text: str) -> None:
        messages = [*history, {"role": "user", "content": user_text}]
        restorer = StreamRestorer(await ctx.store.vault_all(ctx.session_id))
        resp = await self._route(ctx, messages, restorer)
        if resp.text:
            ctx.agent_outputs.append({"agent": "coordinator", "text": resp.text})

        delegations = [tu for tu in resp.tool_uses if tu["name"].startswith("delegate_to_")]
        if not delegations:
            metrics.AGENT_INVOCATIONS.labels("coordinator", "answered_directly").inc()
            return

        specialist_history = history[-SPECIALIST_HISTORY:]
        for tu in delegations:
            await self._delegate(ctx, tu, specialist_history, user_text)

    async def _route(self, ctx: TurnContext, messages: list[Message], restorer: StreamRestorer) -> LLMResponse:
        req = LLMRequest(system=system_prompt("coordinator"), messages=messages, tools=self.tools,
                         max_tokens=512, agent="coordinator", session_id=ctx.session_id,
                         subject=ctx.principal.subject)
        final: LLMResponse | None = None
        async for ev in self.router.stream(req):
            if ev.kind == "text":
                out = restorer.feed(ev.text)
                if out:
                    await ctx.emit("message.delta", {"agent": "coordinator", "text": out})
            else:
                final = ev.response
        tail = restorer.flush()
        if tail:
            await ctx.emit("message.delta", {"agent": "coordinator", "text": tail})
        if final is None:
            raise RuntimeError("LLM stream ended without a final response")
        return final

    async def _delegate(self, ctx: TurnContext, tool_use: dict[str, Any], history: list[Message],
                        fallback_request: str) -> None:
        try:
            agent = AgentName(tool_use["name"].removeprefix("delegate_to_"))
        except ValueError:
            return
        decision = can_delegate(ctx.principal, agent)
        if not decision:
            metrics.GUARDRAIL_EVENTS.labels("delegation_denied").inc()
            text = "You don't have access to this service in the assistant. Please contact your branch."
            await ctx.emit("guardrail", {"type": "authorisation", "message": text})
            ctx.agent_outputs.append({"agent": agent.value, "text": text})
            return
        request = (tool_use.get("input") or {}).get("request") or fallback_request
        await ctx.emit("agent.routed", {"agent": agent.value, "title": SPECS[agent].title})
        await ctx.store.update_state(ctx.session_id, last_agent=agent.value)
        await self.specialists[agent].run(ctx, request, history)
