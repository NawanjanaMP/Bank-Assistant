"""Agent runtime: a bounded tool-use loop shared by all specialist agents."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import date
from functools import cache
from pathlib import Path
from typing import Any

from opentelemetry import trace

from bankai.orchestrator.context import TurnContext
from bankai.orchestrator.guardrails.pii import TOKEN_RE
from bankai.orchestrator.llm.base import LLMRequest, LLMResponse, Message, ToolSpec
from bankai.orchestrator.llm.router import LLMRouter
from bankai.orchestrator.mcp.gateway import MCPGateway
from bankai.orchestrator.observability import metrics
from bankai.shared.config import Settings
from bankai.shared.logging import get_logger
from bankai.shared.policy import AgentName

log = get_logger("bankai.agents")
tracer = trace.get_tracer("bankai.agents")
PROMPTS = Path(__file__).parent / "prompts"


@cache
def load_prompt(name: str) -> str:
    return (PROMPTS / f"{name}.md").read_text(encoding="utf-8")


def system_prompt(name: str) -> str:
    return load_prompt(name) + "\n\n" + load_prompt("_shared_rules").replace("{today}", date.today().isoformat())


class StreamRestorer:
    """Restores PII placeholders in streamed text even when a token is split across deltas."""

    def __init__(self, mapping: dict[str, str]) -> None:
        self.mapping = mapping
        self.buf = ""

    def _restore(self, text: str) -> str:
        return TOKEN_RE.sub(lambda m: self.mapping.get(m.group(0), m.group(0)), text)

    def feed(self, delta: str) -> str:
        self.buf += delta
        cut = self.buf.rfind("[")
        if cut != -1 and "]" not in self.buf[cut:] and len(self.buf) - cut < 40:
            ready, self.buf = self.buf[:cut], self.buf[cut:]
        else:
            ready, self.buf = self.buf, ""
        return self._restore(ready)

    def flush(self) -> str:
        out, self.buf = self._restore(self.buf), ""
        return out


@dataclass(frozen=True)
class AgentSpec:
    name: AgentName
    title: str
    description: str
    servers: tuple[str, ...]  # MCP servers this agent may use
    tools: frozenset[str]  # explicit allow-list across those servers


class SpecialistAgent:
    def __init__(self, spec: AgentSpec, router: LLMRouter, gateway: MCPGateway, settings: Settings) -> None:
        self.spec = spec
        self.router = router
        self.gateway = gateway
        self.s = settings

    async def tools(self) -> list[ToolSpec]:
        specs: list[ToolSpec] = []
        for server in self.spec.servers:
            specs += [t for t in await self.gateway.list_tools(server) if t.name in self.spec.tools]
        return specs

    async def run(self, ctx: TurnContext, request: str, history: list[Message]) -> str:
        """Run the agent on a (tokenised) request; stream its answer; return the tokenised final text."""
        agent = self.spec.name.value
        started = time.perf_counter()
        await ctx.emit("agent.started", {"agent": agent, "title": self.spec.title})
        messages: list[Message] = [*history, {"role": "user", "content": request}]
        tools = await self.tools()
        system = system_prompt(agent)
        restorer = StreamRestorer(await ctx.store.vault_all(ctx.session_id))
        final_text = ""
        outcome = "ok"
        with tracer.start_as_current_span("agent.run") as span:
            span.set_attribute("agent", agent)
            steps = 0
            for step in range(1, self.s.agent_max_steps + 1):
                steps = step
                resp = await self._stream_step(ctx, LLMRequest(
                    system=system, messages=messages, tools=tools, max_tokens=self.s.llm_max_output_tokens,
                    agent=agent, session_id=ctx.session_id, subject=ctx.principal.subject), restorer)
                messages.append({"role": "assistant", "content": resp.content})
                if resp.text:
                    final_text = resp.text
                if not resp.tool_uses:
                    break
                results = await asyncio.gather(*(self._run_tool(ctx, tu) for tu in resp.tool_uses))
                messages.append({"role": "user", "content": list(results)})
                # Refresh vault view: tool results may have introduced new placeholders.
                restorer.mapping = await ctx.store.vault_all(ctx.session_id)
            else:
                outcome = "max_steps"
                final_text = final_text or "Sorry, I couldn't complete that request. Please try rephrasing it."
                await ctx.emit("message.delta", {"agent": agent, "text": final_text})
            span.set_attribute("agent.steps", steps)
        metrics.AGENT_INVOCATIONS.labels(agent, outcome).inc()
        metrics.AGENT_STEPS.labels(agent).observe(steps)
        await ctx.emit("agent.completed", {"agent": agent, "latency_ms": round((time.perf_counter() - started) * 1000)})
        ctx.agent_outputs.append({"agent": agent, "text": final_text})
        return final_text

    async def _stream_step(self, ctx: TurnContext, req: LLMRequest, restorer: StreamRestorer) -> LLMResponse:
        final: LLMResponse | None = None
        async for ev in self.router.stream(req):
            if ev.kind == "text":
                out = restorer.feed(ev.text)
                if out:
                    await ctx.emit("message.delta", {"agent": req.agent, "text": out})
            else:
                final = ev.response
        tail = restorer.flush()
        if tail:
            await ctx.emit("message.delta", {"agent": req.agent, "text": tail})
        if final and final.text and final.tool_uses:
            await ctx.emit("message.break", {"agent": req.agent})  # interim text before tool calls
        if final is None:
            raise RuntimeError("LLM stream ended without a final response")
        return final

    async def _run_tool(self, ctx: TurnContext, tool_use: dict[str, Any]) -> dict[str, Any]:
        agent = self.spec.name.value
        call_id, name = tool_use["id"], tool_use["name"]
        started = time.perf_counter()
        await ctx.emit("tool.started", {"agent": agent, "tool": name, "call_id": call_id})
        outcome = await self.gateway.call(ctx, agent, self.spec.tools, name, tool_use.get("input") or {})
        await ctx.emit("tool.completed", {
            "agent": agent, "tool": name, "call_id": call_id, "status": outcome.status,
            "latency_ms": round((time.perf_counter() - started) * 1000), "data": outcome.display,
        })
        block: dict[str, Any] = {"type": "tool_result", "tool_use_id": call_id, "content": outcome.model_content}
        if outcome.is_error:
            block["is_error"] = True
        return block
