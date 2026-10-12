"""Session Store (Redis): conversation history, inter-agent shared state, PII vault, pending actions.

Layout (all keys expire with the session TTL, refreshed on activity):

    sess:{id}:meta      hash   owner, created_at, title, last_agent
    sess:{id}:history   list   JSON messages, *already PII-tokenised*
    sess:{id}:state     hash   inter-agent shared state (selected account, open intents ...)
    sess:{id}:vault     hash   token -> Fernet-encrypted original value
    sess:{id}:action:{a} string pending write action awaiting customer confirmation
    sess:{id}:lock      string per-session turn lock (one turn at a time)
    user:{sub}:sessions zset   session ids by last activity
"""

from __future__ import annotations

import json
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from redis.asyncio import Redis

from bankai.shared.config import Settings


class SessionNotFound(Exception):
    pass


class SessionBusy(Exception):
    pass


class SessionStore:
    def __init__(self, redis: Redis, settings: Settings) -> None:
        self.r = redis
        self.ttl = settings.session_ttl_seconds
        self.max_history = settings.max_history_messages
        key = settings.pii_vault_key.get_secret_value()
        # Local/test without a configured key get an ephemeral one (vault entries die with the process).
        self._fernet = Fernet(key.encode() if key else Fernet.generate_key())

    # ---------------------------------------------------------------- sessions
    async def create(self, owner: str, title: str = "New conversation") -> str:
        sid = uuid.uuid4().hex
        now = time.time()
        async with self.r.pipeline(transaction=True) as p:
            p.hset(f"sess:{sid}:meta", mapping={"owner": owner, "created_at": now, "updated_at": now,
                                                 "title": title})
            p.expire(f"sess:{sid}:meta", self.ttl)
            p.zadd(f"user:{owner}:sessions", {sid: now})
            p.expire(f"user:{owner}:sessions", self.ttl * 4)
            await p.execute()
        return sid

    async def get_meta(self, sid: str, owner: str) -> dict[str, str]:
        meta = await self.r.hgetall(f"sess:{sid}:meta")
        # Not-found and not-yours are indistinguishable to the caller on purpose.
        if not meta or meta.get(b"owner", b"").decode() != owner:
            raise SessionNotFound(sid)
        return {k.decode(): v.decode() for k, v in meta.items()}

    async def touch(self, sid: str, owner: str, **fields: Any) -> None:
        now = time.time()
        async with self.r.pipeline(transaction=True) as p:
            if fields:
                p.hset(f"sess:{sid}:meta", mapping={k: str(v) for k, v in fields.items()})
            p.hset(f"sess:{sid}:meta", "updated_at", now)
            for suffix in ("meta", "history", "state", "vault"):
                p.expire(f"sess:{sid}:{suffix}", self.ttl)
            p.zadd(f"user:{owner}:sessions", {sid: now})
            await p.execute()

    async def list_sessions(self, owner: str, limit: int = 20) -> list[dict[str, str]]:
        ids = await self.r.zrevrange(f"user:{owner}:sessions", 0, limit - 1)
        out = []
        for raw in ids:
            sid = raw.decode()
            meta = await self.r.hgetall(f"sess:{sid}:meta")
            if meta:
                out.append({"id": sid, **{k.decode(): v.decode() for k, v in meta.items() if k != b"owner"}})
            else:
                await self.r.zrem(f"user:{owner}:sessions", sid)
        return out

    async def delete(self, sid: str, owner: str) -> None:
        await self.get_meta(sid, owner)
        keys = [k async for k in self.r.scan_iter(match=f"sess:{sid}:*")]
        if keys:
            await self.r.delete(*keys)
        await self.r.zrem(f"user:{owner}:sessions", sid)

    @asynccontextmanager
    async def turn_lock(self, sid: str, lock_ttl: int = 120):
        key = f"sess:{sid}:lock"
        token = uuid.uuid4().hex
        if not await self.r.set(key, token, nx=True, ex=lock_ttl):
            raise SessionBusy(sid)
        try:
            yield
        finally:
            # Compare-and-delete so we never release a lock that expired and was re-acquired.
            await self.r.eval(
                "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) end return 0",
                1, key, token,
            )

    # ---------------------------------------------------------------- history
    async def append(self, sid: str, *messages: dict[str, Any]) -> None:
        if not messages:
            return
        key = f"sess:{sid}:history"
        async with self.r.pipeline(transaction=True) as p:
            p.rpush(key, *[json.dumps(m, default=str) for m in messages])
            p.ltrim(key, -self.max_history, -1)
            p.expire(key, self.ttl)
            await p.execute()

    async def history(self, sid: str) -> list[dict[str, Any]]:
        raw = await self.r.lrange(f"sess:{sid}:history", 0, -1)
        msgs = [json.loads(m) for m in raw]
        # Never start a model context on an orphaned tool_result after trimming.
        while msgs and msgs[0]["role"] != "user" or (msgs and _is_tool_result(msgs[0])):
            msgs.pop(0)
        return msgs

    # ---------------------------------------------------------------- shared state
    async def get_state(self, sid: str) -> dict[str, Any]:
        raw = await self.r.hgetall(f"sess:{sid}:state")
        return {k.decode(): json.loads(v) for k, v in raw.items()}

    async def update_state(self, sid: str, **values: Any) -> None:
        if values:
            await self.r.hset(f"sess:{sid}:state", mapping={k: json.dumps(v, default=str) for k, v in values.items()})
            await self.r.expire(f"sess:{sid}:state", self.ttl)

    # ---------------------------------------------------------------- PII vault
    async def vault_put(self, sid: str, token: str, value: str) -> None:
        await self.r.hset(f"sess:{sid}:vault", token, self._fernet.encrypt(value.encode()))
        await self.r.expire(f"sess:{sid}:vault", self.ttl)

    async def vault_all(self, sid: str) -> dict[str, str]:
        out = {}
        for k, v in (await self.r.hgetall(f"sess:{sid}:vault")).items():
            try:
                out[k.decode()] = self._fernet.decrypt(v).decode()
            except InvalidToken:  # key rotated: entry unusable, drop silently
                continue
        return out

    # ---------------------------------------------------------------- pending actions
    async def put_action(self, sid: str, action_id: str, action: dict[str, Any], ttl: int = 600) -> None:
        await self.r.set(f"sess:{sid}:action:{action_id}", json.dumps(action, default=str), ex=ttl)

    async def get_action(self, sid: str, action_id: str) -> dict[str, Any] | None:
        raw = await self.r.get(f"sess:{sid}:action:{action_id}")
        return json.loads(raw) if raw else None

    async def take_action(self, sid: str, action_id: str) -> dict[str, Any] | None:
        """Atomically fetch-and-delete: a confirmation can only ever execute once."""
        raw = await self.r.getdel(f"sess:{sid}:action:{action_id}")
        return json.loads(raw) if raw else None


def _is_tool_result(msg: dict[str, Any]) -> bool:
    content = msg.get("content")
    return isinstance(content, list) and any(b.get("type") == "tool_result" for b in content)
