from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import fakeredis
import httpx
import pytest

from bankai.core_banking.client import InMemoryCoreBank
from bankai.mcp_servers import accounts, service, transactions
from bankai.orchestrator.db import Ledger
from bankai.orchestrator.llm.simulator import SimulatorLLM
from bankai.orchestrator.main import create_app
from bankai.shared.config import AuthMode, Environment, Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(
        env=Environment.TEST, auth_mode=AuthMode.DEV, llm_primary="simulator", llm_fallback=None,
        context_token_signing_key="test-signing-key-0123456789abcdef-0123456789",
        user_rate_limit_per_minute=1000, database_url="sqlite+aiosqlite:///:memory:", log_level="WARNING",
    )


@pytest.fixture
def bank() -> InMemoryCoreBank:
    return InMemoryCoreBank()


@pytest.fixture
def mcp_targets(bank: InMemoryCoreBank, settings: Settings) -> dict[str, Any]:
    return {
        "accounts": accounts.build(bank, settings),
        "transactions": transactions.build(bank, settings),
        "service": service.build(bank, settings),
    }


@pytest.fixture
async def redis():
    r = fakeredis.FakeAsyncRedis()
    yield r
    await r.aclose()


@pytest.fixture
async def app(settings, redis, mcp_targets):
    ledger = Ledger.from_url(settings.database_url)
    application = create_app(settings, redis=redis, mcp_targets=mcp_targets,
                             llm_clients={"simulator": SimulatorLLM()}, ledger=ledger)
    async with application.router.lifespan_context(application):
        yield application
    await ledger.dispose()


@pytest.fixture
async def client(app) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                 headers={"Authorization": "Bearer dev:C-1001"}) as c:
        yield c


def parse_sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    events = []
    for chunk in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in chunk.splitlines() if not line.startswith(":") and ": " in line)
        if "event" in lines:
            events.append((lines["event"], json.loads(lines["data"])))
    return events


async def chat(client: httpx.AsyncClient, sid: str, text: str) -> list[tuple[str, dict[str, Any]]]:
    resp = await client.post(f"/v1/sessions/{sid}/messages", json={"text": text})
    assert resp.status_code == 200, resp.text
    return parse_sse(resp.text)


def answer(events) -> str:  # noqa: ANN001
    return "".join(d["text"] for e, d in events if e == "message.delta")
