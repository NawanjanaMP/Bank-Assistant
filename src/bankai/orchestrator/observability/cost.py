"""Cost Tracker: per-call token accounting, spend estimation and budget enforcement.

* Every LLM response is priced from ``PRICING`` (USD per million tokens) and recorded to
  Prometheus, to fast Redis counters (session + user/day, used for budget checks) and to the
  durable usage ledger in Postgres (finance reporting / chargeback).
* Budgets are checked before each turn: a runaway loop or abusive client is stopped by
  money, not just by request rate.
* Self-hosted inference is priced at an internal per-token rate so its capacity cost is
  visible on the same dashboard.

Keep PRICING in sync with your contract: https://platform.claude.com/docs/en/about-claude/pricing
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from redis.asyncio import Redis

from bankai.orchestrator.llm.base import LLMRequest, LLMResponse
from bankai.orchestrator.observability import metrics
from bankai.shared.config import Settings


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    cache_read: float
    cache_write: float  # 5-minute cache write


PRICING: dict[str, Price] = {
    "claude-opus-5": Price(4.00, 20.00, 0.20, 5.00),
    "claude-sonnet-5": Price(2.00, 10.00, 0.10, 2.50),
    "claude-haiku-4-5-20251001": Price(0.10, 0.50, 0.01, 0.125),  # prompts <= 100K tokens
    "self_hosted": Price(0.05, 0.15, 0.0, 0.0),  # internal GPU chargeback rate - set from your FinOps model
    "simulator": Price(0.0, 0.0, 0.0, 0.0),
}


def price_of(resp: LLMResponse) -> float:
    p = PRICING.get(resp.model) or PRICING.get(resp.provider) or PRICING["self_hosted"]
    u = resp.usage
    return (u.input_tokens * p.input + u.output_tokens * p.output + u.cache_read_tokens * p.cache_read
            + u.cache_write_tokens * p.cache_write) / 1_000_000


class BudgetExceeded(Exception):
    pass


class CostTracker:
    def __init__(self, redis: Redis, settings: Settings, ledger=None) -> None:  # noqa: ANN001
        self.r = redis
        self.s = settings
        self.ledger = ledger

    def _day_key(self, subject: str) -> str:
        return f"cost:user:{subject}:{datetime.now(UTC):%Y%m%d}"

    async def record(self, req: LLMRequest, resp: LLMResponse) -> None:
        cost = price_of(resp)
        labels = (resp.provider, resp.model, req.agent)
        metrics.LLM_CALLS.labels(*labels).inc()
        metrics.LLM_COST.labels(*labels).inc(cost)
        metrics.LLM_LATENCY.labels(resp.provider, req.agent).observe(resp.latency_ms / 1000)
        for kind, n in (("input", resp.usage.input_tokens), ("output", resp.usage.output_tokens),
                        ("cache_read", resp.usage.cache_read_tokens), ("cache_write", resp.usage.cache_write_tokens)):
            if n:
                metrics.LLM_TOKENS.labels(*labels, kind).inc(n)
        if not req.session_id:
            return
        subject = req.subject or "unknown"
        async with self.r.pipeline(transaction=False) as p:
            p.hincrbyfloat(f"cost:sess:{req.session_id}", "usd", cost)
            p.hincrby(f"cost:sess:{req.session_id}", "input_tokens", resp.usage.input_tokens)
            p.hincrby(f"cost:sess:{req.session_id}", "output_tokens", resp.usage.output_tokens)
            p.expire(f"cost:sess:{req.session_id}", self.s.session_ttl_seconds * 2)
            p.incrbyfloat(self._day_key(subject), cost)
            p.expire(self._day_key(subject), 60 * 60 * 48)
            await p.execute()
        if self.ledger is not None:
            await self.ledger.record_usage(
                session_id=req.session_id, subject=subject, agent=req.agent, provider=resp.provider,
                model=resp.model, input_tokens=resp.usage.input_tokens, output_tokens=resp.usage.output_tokens,
                cache_read_tokens=resp.usage.cache_read_tokens, cache_write_tokens=resp.usage.cache_write_tokens,
                cost_usd=cost, latency_ms=resp.latency_ms,
            )

    async def session_usage(self, session_id: str) -> dict[str, float]:
        raw = await self.r.hgetall(f"cost:sess:{session_id}")
        return {k.decode(): float(v) for k, v in raw.items()}

    async def check_budget(self, session_id: str, subject: str) -> None:
        sess = float((await self.r.hget(f"cost:sess:{session_id}", "usd")) or 0)
        day = float((await self.r.get(self._day_key(subject))) or 0)
        if sess >= self.s.session_cost_limit_usd or day >= self.s.user_daily_cost_limit_usd:
            metrics.GUARDRAIL_EVENTS.labels("budget_exceeded").inc()
            raise BudgetExceeded("usage limit reached")
