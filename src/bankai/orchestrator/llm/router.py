"""LLM router: chooses Self-hosted vs Third-party per request, with failover + circuit breaking.

Routing rules (in order):
1. Agents listed in ``self_hosted_only_agents`` never leave the bank network - no
   third-party fallback, even during an outage (fail closed).
2. Otherwise try ``llm_primary`` then ``llm_fallback``.
3. A provider whose circuit is open is skipped until its cool-down passes.
4. A stream only fails over if it has not emitted any text yet (no half-answers).
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

from opentelemetry import trace

from bankai.orchestrator.llm.base import LLMClient, LLMError, LLMRequest, LLMResponse, StreamEvent
from bankai.shared.config import Settings
from bankai.shared.logging import get_logger

log = get_logger("bankai.llm")
tracer = trace.get_tracer("bankai.llm")

UsageHook = Callable[[LLMRequest, LLMResponse], Awaitable[None]]


@dataclass
class CircuitBreaker:
    threshold: int
    reset_after: float
    failures: int = 0
    opened_at: float | None = None

    @property
    def open(self) -> bool:
        if self.opened_at is None:
            return False
        if time.monotonic() - self.opened_at >= self.reset_after:
            return False  # half-open: allow a trial request
        return True

    def success(self) -> None:
        self.failures = 0
        self.opened_at = None

    def failure(self) -> None:
        self.failures += 1
        if self.failures >= self.threshold:
            self.opened_at = time.monotonic()


class NoProviderAvailable(LLMError):
    pass


class LLMRouter:
    def __init__(self, clients: dict[str, LLMClient], settings: Settings, on_usage: UsageHook | None = None) -> None:
        self.clients = clients
        self.s = settings
        self.on_usage = on_usage
        self.breakers = {
            name: CircuitBreaker(settings.circuit_breaker_failures, settings.circuit_breaker_reset_seconds)
            for name in clients
        }

    def chain_for(self, agent: str) -> list[str]:
        if agent in self.s.self_hosted_only_agents:
            chain = ["self_hosted"]
        else:
            chain = [self.s.llm_primary] + ([self.s.llm_fallback] if self.s.llm_fallback else [])
        return [p for p in dict.fromkeys(chain) if p in self.clients]

    def model_for(self, provider: str, agent: str) -> str:
        if provider == "anthropic":
            return self.s.coordinator_model if agent == "coordinator" else self.s.specialist_model
        if provider == "self_hosted":
            return self.s.self_hosted_model
        return provider

    async def _record(self, req: LLMRequest, resp: LLMResponse) -> None:
        if self.on_usage:
            try:
                await self.on_usage(req, resp)
            except Exception:  # pragma: no cover - accounting must never break a turn
                log.exception("usage_hook_failed")

    async def complete(self, req: LLMRequest) -> LLMResponse:
        last: Exception | None = None
        for provider in self.chain_for(req.agent):
            breaker = self.breakers[provider]
            if breaker.open:
                continue
            model = self.model_for(provider, req.agent)
            with tracer.start_as_current_span("llm.complete") as span:
                span.set_attributes({"llm.provider": provider, "llm.model": model, "agent": req.agent})
                try:
                    resp = await self.clients[provider].complete(req, model)
                except LLMError as exc:
                    breaker.failure()
                    span.record_exception(exc)
                    log.warning("llm_failover", provider=provider, agent=req.agent, error=str(exc))
                    last = exc
                    continue
                breaker.success()
                span.set_attributes({"llm.input_tokens": resp.usage.input_tokens,
                                     "llm.output_tokens": resp.usage.output_tokens})
            await self._record(req, resp)
            return resp
        raise NoProviderAvailable(f"no LLM provider available for agent '{req.agent}': {last}", retryable=True)

    async def stream(self, req: LLMRequest) -> AsyncIterator[StreamEvent]:
        last: Exception | None = None
        for provider in self.chain_for(req.agent):
            breaker = self.breakers[provider]
            if breaker.open:
                continue
            model = self.model_for(provider, req.agent)
            emitted = False
            try:
                async for ev in self.clients[provider].stream(req, model):
                    if ev.kind == "text":
                        emitted = True
                    elif ev.response is not None:
                        breaker.success()
                        await self._record(req, ev.response)
                    yield ev
                return
            except LLMError as exc:
                breaker.failure()
                last = exc
                log.warning("llm_stream_failover", provider=provider, agent=req.agent, error=str(exc))
                if emitted:
                    raise
        raise NoProviderAvailable(f"no LLM provider available for agent '{req.agent}': {last}", retryable=True)
