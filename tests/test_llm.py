"""LLM routing, failover, circuit breaking, cost and provider adapters."""

from __future__ import annotations

import pytest

from bankai.orchestrator.llm.base import LLMError, LLMRequest, LLMResponse, StreamEvent, Usage
from bankai.orchestrator.llm.openai_compat_client import to_openai_messages
from bankai.orchestrator.llm.router import LLMRouter, NoProviderAvailable
from bankai.orchestrator.observability.cost import price_of
from bankai.shared.config import Environment, Settings


class Fake:
    def __init__(self, provider: str, fail: bool = False) -> None:
        self.provider = provider
        self.fail = fail
        self.calls = 0

    async def complete(self, req, model):  # noqa: ANN001
        self.calls += 1
        if self.fail:
            raise LLMError("boom")
        return LLMResponse([{"type": "text", "text": self.provider}], "end_turn", Usage(10, 5), model, self.provider)

    async def stream(self, req, model):  # noqa: ANN001
        resp = await self.complete(req, model)
        yield StreamEvent("text", text=resp.text)
        yield StreamEvent("final", response=resp)


def _settings(**kw) -> Settings:
    return Settings(env=Environment.TEST, llm_primary="anthropic", llm_fallback="self_hosted",
                    circuit_breaker_failures=2, **kw)


async def test_failover_to_self_hosted():
    primary, backup = Fake("anthropic", fail=True), Fake("self_hosted")
    router = LLMRouter({"anthropic": primary, "self_hosted": backup}, _settings())
    resp = await router.complete(LLMRequest("s", [], agent="accounts"))
    assert resp.provider == "self_hosted"


async def test_circuit_opens_and_skips_failing_provider():
    primary, backup = Fake("anthropic", fail=True), Fake("self_hosted")
    router = LLMRouter({"anthropic": primary, "self_hosted": backup}, _settings())
    for _ in range(4):
        await router.complete(LLMRequest("s", [], agent="accounts"))
    assert primary.calls == 2  # circuit opened after 2 failures


async def test_self_hosted_only_agent_never_uses_third_party():
    primary, backup = Fake("anthropic"), Fake("self_hosted", fail=True)
    router = LLMRouter({"anthropic": primary, "self_hosted": backup}, _settings(self_hosted_only_agents=["service"]))
    with pytest.raises(NoProviderAvailable):
        await router.complete(LLMRequest("s", [], agent="service"))
    assert primary.calls == 0  # fail closed


async def test_stream_failover_and_usage_hook():
    seen = []

    async def hook(req, resp):  # noqa: ANN001
        seen.append(resp.provider)

    router = LLMRouter({"anthropic": Fake("anthropic", fail=True), "self_hosted": Fake("self_hosted")},
                       _settings(), on_usage=hook)
    events = [e async for e in router.stream(LLMRequest("s", [], agent="coordinator"))]
    assert events[0].text == "self_hosted" and seen == ["self_hosted"]


def test_model_selection_per_agent():
    router = LLMRouter({"anthropic": Fake("anthropic")}, _settings())
    assert router.model_for("anthropic", "coordinator") == "claude-haiku-4-5-20251001"
    assert router.model_for("anthropic", "accounts") == "claude-sonnet-5"


def test_pricing():
    resp = LLMResponse([], "end_turn", Usage(1_000_000, 100_000, 0, 0), "claude-sonnet-5", "anthropic")
    assert price_of(resp) == pytest.approx(2.0 + 1.0)


def test_openai_translation_of_tool_loop():
    msgs = [
        {"role": "user", "content": "balance?"},
        {"role": "assistant", "content": [{"type": "text", "text": "checking"},
                                          {"type": "tool_use", "id": "t1", "name": "get_balance",
                                           "input": {"account_id": "A-1"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "{\"a\":1}"}]},
    ]
    out = to_openai_messages("sys", msgs)
    assert [m["role"] for m in out] == ["system", "user", "assistant", "tool"]
    assert out[2]["tool_calls"][0]["function"]["name"] == "get_balance"
    assert out[3]["tool_call_id"] == "t1"


async def test_anthropic_adapter_parses_tool_use_and_usage():
    import json as _json

    try:  # newer anthropic SDKs are built on httpx2, older ones on httpx
        import httpx2 as httpx
    except ImportError:
        import httpx

    from bankai.orchestrator.llm.anthropic_client import AnthropicClient
    from bankai.orchestrator.llm.base import ToolSpec

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(_json.loads(request.content))
        return httpx.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
            "stop_reason": "tool_use", "stop_sequence": None,
            "content": [{"type": "text", "text": "Checking."},
                        {"type": "tool_use", "id": "toolu_1", "name": "get_balance", "input": {"account_id": "A-1"}}],
            "usage": {"input_tokens": 120, "output_tokens": 30, "cache_read_input_tokens": 100,
                      "cache_creation_input_tokens": 0},
        })

    client = AnthropicClient("test-key")
    client._client = client._client.with_options(http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    req = LLMRequest("system rules", [{"role": "user", "content": "balance?"}],
                     tools=[ToolSpec("get_balance", "d", {"type": "object", "properties": {}})],
                     agent="accounts", session_id="sess-1", subject="never-sent")
    resp = await client.complete(req, "claude-sonnet-5-5")
    assert resp.tool_uses[0]["input"] == {"account_id": "A-1"} and resp.text == "Checking."
    assert resp.usage.cache_read_tokens == 100
    assert seen["metadata"] == {"user_id": "sess-1"} and "never-sent" not in _json.dumps(seen)
    assert seen["system"][0]["cache_control"] == {"type": "ephemeral"}
