"""Per-customer rate limit (second layer behind the edge's per-IP limits).

Sliding-window counter in Redis so the limit holds across all orchestrator replicas.
"""

from __future__ import annotations

import time

from redis.asyncio import Redis

_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local count = redis.call('ZCARD', key)
if count >= limit then
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  return {0, math.ceil((tonumber(oldest[2]) + window - now))}
end
redis.call('ZADD', key, now, ARGV[4])
redis.call('EXPIRE', key, window)
return {1, 0}
"""


class RateLimited(Exception):
    def __init__(self, retry_after: int) -> None:
        super().__init__("rate limited")
        self.retry_after = max(1, retry_after)


class RateLimiter:
    def __init__(self, redis: Redis, per_minute: int) -> None:
        self.r = redis
        self.limit = per_minute

    async def hit(self, subject: str) -> None:
        now = time.time()
        allowed, retry = await self.r.eval(_SCRIPT, 1, f"rl:user:{subject}", now, 60, self.limit, f"{now}")
        if not int(allowed):
            raise RateLimited(int(retry))
