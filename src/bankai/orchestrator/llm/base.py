"""Provider-neutral LLM interface.

The internal message format is Anthropic-style content blocks (text / tool_use /
tool_result); adapters translate for other providers. Agents never import a vendor SDK.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

Message = dict[str, Any]  # {"role": "user"|"assistant", "content": str | list[block]}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def __iadd__(self, other: Usage) -> Usage:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.cache_write_tokens += other.cache_write_tokens
        return self


@dataclass
class LLMResponse:
    content: list[dict[str, Any]]
    stop_reason: str
    usage: Usage
    model: str
    provider: str
    latency_ms: float = 0.0

    @property
    def text(self) -> str:
        return "".join(b.get("text", "") for b in self.content if b.get("type") == "text")

    @property
    def tool_uses(self) -> list[dict[str, Any]]:
        return [b for b in self.content if b.get("type") == "tool_use"]


@dataclass
class StreamEvent:
    kind: Literal["text", "final"]
    text: str = ""
    response: LLMResponse | None = None


@dataclass
class LLMRequest:
    system: str
    messages: list[Message]
    tools: list[ToolSpec] = field(default_factory=list)
    max_tokens: int = 1024
    tool_choice: Literal["auto", "any", "none"] = "auto"
    agent: str = "unknown"
    session_id: str | None = None
    subject: str | None = None  # for internal cost accounting only; never sent to a provider


class LLMError(Exception):
    def __init__(self, message: str, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class LLMClient(Protocol):
    provider: str

    async def complete(self, req: LLMRequest, model: str) -> LLMResponse: ...

    def stream(self, req: LLMRequest, model: str) -> AsyncIterator[StreamEvent]: ...
