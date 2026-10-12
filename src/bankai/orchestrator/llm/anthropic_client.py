"""Third-party LLM: Claude via the Anthropic API."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

import anthropic

from bankai.orchestrator.llm.base import LLMError, LLMRequest, LLMResponse, StreamEvent, Usage


class AnthropicClient:
    provider = "anthropic"

    def __init__(self, api_key: str, base_url: str | None = None, timeout: float = 45.0) -> None:
        # SDK retries transient errors (429/5xx/connection) with backoff; router adds failover on top.
        self._client = anthropic.AsyncAnthropic(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=2)

    def _params(self, req: LLMRequest, model: str) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": model,
            "max_tokens": req.max_tokens,
            # Cache the (long, static) system prompt + tool definitions across turns.
            "system": [{"type": "text", "text": req.system, "cache_control": {"type": "ephemeral"}}],
            "messages": req.messages,
            # Opaque per-session id only: never send customer identifiers to a third party.
            "metadata": {"user_id": req.session_id or "anonymous"},
        }
        if req.tools:
            params["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in req.tools
            ]
            if req.tool_choice != "auto":
                params["tool_choice"] = {"type": req.tool_choice}
        return params

    @staticmethod
    def _to_response(msg: Any, latency_ms: float) -> LLMResponse:
        u = msg.usage
        return LLMResponse(
            content=[b.model_dump(exclude_none=True, include={"type", "text", "id", "name", "input"})
                     for b in msg.content if b.type in ("text", "tool_use")],
            stop_reason=msg.stop_reason or "end_turn",
            usage=Usage(
                input_tokens=u.input_tokens or 0,
                output_tokens=u.output_tokens or 0,
                cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
                cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
            ),
            model=msg.model,
            provider="anthropic",
            latency_ms=latency_ms,
        )

    async def complete(self, req: LLMRequest, model: str) -> LLMResponse:
        started = time.perf_counter()
        try:
            msg = await self._client.messages.create(**self._params(req, model))
        except anthropic.APIStatusError as exc:
            raise LLMError(f"anthropic {exc.status_code}", retryable=exc.status_code >= 429) from exc
        except anthropic.APIError as exc:
            raise LLMError(f"anthropic error: {type(exc).__name__}") from exc
        return self._to_response(msg, (time.perf_counter() - started) * 1000)

    async def stream(self, req: LLMRequest, model: str) -> AsyncIterator[StreamEvent]:
        started = time.perf_counter()
        try:
            async with self._client.messages.stream(**self._params(req, model)) as s:
                async for delta in s.text_stream:
                    yield StreamEvent("text", text=delta)
                final = await s.get_final_message()
        except anthropic.APIStatusError as exc:
            raise LLMError(f"anthropic {exc.status_code}", retryable=exc.status_code >= 429) from exc
        except anthropic.APIError as exc:
            raise LLMError(f"anthropic error: {type(exc).__name__}") from exc
        yield StreamEvent("final", response=self._to_response(final, (time.perf_counter() - started) * 1000))
