"""Self-hosted LLM via an OpenAI-compatible endpoint (vLLM, TGI, Ollama, LM Studio ...).

Translates the internal Anthropic-style block format to/from chat-completions.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any

import openai

from bankai.orchestrator.llm.base import LLMError, LLMRequest, LLMResponse, Message, StreamEvent, Usage


def to_openai_messages(system: str, messages: list[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for m in messages:
        content = m["content"]
        if isinstance(content, str):
            out.append({"role": m["role"], "content": content})
            continue
        if m["role"] == "assistant":
            text = "".join(b.get("text", "") for b in content if b["type"] == "text")
            calls = [
                {"id": b["id"], "type": "function",
                 "function": {"name": b["name"], "arguments": json.dumps(b.get("input", {}))}}
                for b in content if b["type"] == "tool_use"
            ]
            msg: dict[str, Any] = {"role": "assistant", "content": text or None}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
        else:
            texts = []
            for b in content:
                if b["type"] == "tool_result":
                    body = b["content"] if isinstance(b["content"], str) else json.dumps(b["content"])
                    out.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": body})
                elif b["type"] == "text":
                    texts.append(b["text"])
            if texts:
                out.append({"role": "user", "content": "\n".join(texts)})
    return out


def _tools(req: LLMRequest) -> dict[str, Any]:
    if not req.tools:
        return {}
    params: dict[str, Any] = {
        "tools": [{"type": "function", "function": {"name": t.name, "description": t.description,
                                                     "parameters": t.input_schema}} for t in req.tools]
    }
    params["tool_choice"] = {"auto": "auto", "any": "required", "none": "none"}[req.tool_choice]
    return params


def _parse_args(raw: str | None) -> dict[str, Any]:
    try:
        val = json.loads(raw or "{}")
        return val if isinstance(val, dict) else {}
    except json.JSONDecodeError:
        return {}


class OpenAICompatClient:
    provider = "self_hosted"

    def __init__(self, base_url: str, api_key: str, timeout: float = 45.0) -> None:
        self._client = openai.AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=timeout, max_retries=1)

    async def complete(self, req: LLMRequest, model: str) -> LLMResponse:
        started = time.perf_counter()
        try:
            resp = await self._client.chat.completions.create(
                model=model, messages=to_openai_messages(req.system, req.messages),
                max_tokens=req.max_tokens, **_tools(req),
            )
        except openai.APIStatusError as exc:
            raise LLMError(f"self-hosted {exc.status_code}", retryable=exc.status_code >= 429) from exc
        except openai.APIError as exc:
            raise LLMError(f"self-hosted error: {type(exc).__name__}") from exc
        choice = resp.choices[0]
        blocks: list[dict[str, Any]] = []
        if choice.message.content:
            blocks.append({"type": "text", "text": choice.message.content})
        for c in choice.message.tool_calls or []:
            blocks.append({"type": "tool_use", "id": c.id, "name": c.function.name,
                           "input": _parse_args(c.function.arguments)})
        return LLMResponse(
            content=blocks,
            stop_reason="tool_use" if choice.message.tool_calls else "end_turn",
            usage=Usage(resp.usage.prompt_tokens if resp.usage else 0,
                        resp.usage.completion_tokens if resp.usage else 0),
            model=model, provider=self.provider, latency_ms=(time.perf_counter() - started) * 1000,
        )

    async def stream(self, req: LLMRequest, model: str) -> AsyncIterator[StreamEvent]:
        started = time.perf_counter()
        text_parts: list[str] = []
        calls: dict[int, dict[str, Any]] = {}
        usage = Usage()
        try:
            stream = await self._client.chat.completions.create(
                model=model, messages=to_openai_messages(req.system, req.messages), max_tokens=req.max_tokens,
                stream=True, stream_options={"include_usage": True}, **_tools(req),
            )
            async for chunk in stream:
                if chunk.usage:
                    usage = Usage(chunk.usage.prompt_tokens, chunk.usage.completion_tokens)
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta.content:
                    text_parts.append(delta.content)
                    yield StreamEvent("text", text=delta.content)
                for tc in delta.tool_calls or []:
                    slot = calls.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                    slot["id"] = tc.id or slot["id"]
                    if tc.function:
                        slot["name"] += tc.function.name or ""
                        slot["args"] += tc.function.arguments or ""
        except openai.APIStatusError as exc:
            raise LLMError(f"self-hosted {exc.status_code}", retryable=exc.status_code >= 429) from exc
        except openai.APIError as exc:
            raise LLMError(f"self-hosted error: {type(exc).__name__}") from exc
        blocks: list[dict[str, Any]] = [{"type": "text", "text": "".join(text_parts)}] if text_parts else []
        blocks += [{"type": "tool_use", "id": c["id"] or f"call_{i}", "name": c["name"],
                    "input": _parse_args(c["args"])} for i, c in sorted(calls.items())]
        yield StreamEvent("final", response=LLMResponse(
            content=blocks, stop_reason="tool_use" if calls else "end_turn", usage=usage, model=model,
            provider=self.provider, latency_ms=(time.perf_counter() - started) * 1000))
