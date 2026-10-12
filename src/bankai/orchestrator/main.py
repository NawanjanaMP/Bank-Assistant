"""Orchestrator service (the "API" box): wires every component together."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import make_asgi_app
from redis.asyncio import Redis

from bankai.orchestrator.agents.coordinator import Coordinator
from bankai.orchestrator.api.routes import router as api_router
from bankai.orchestrator.db import Ledger
from bankai.orchestrator.guardrails.pii import PiiRedactor
from bankai.orchestrator.llm.anthropic_client import AnthropicClient
from bankai.orchestrator.llm.base import LLMClient
from bankai.orchestrator.llm.openai_compat_client import OpenAICompatClient
from bankai.orchestrator.llm.router import LLMRouter
from bankai.orchestrator.llm.simulator import SimulatorLLM
from bankai.orchestrator.mcp.gateway import MCPGateway, ServerTarget
from bankai.orchestrator.observability.cost import CostTracker
from bankai.orchestrator.observability.tracing import setup_tracing
from bankai.orchestrator.security.oidc import TokenVerifier
from bankai.orchestrator.security.ratelimit import RateLimiter
from bankai.orchestrator.service import TurnService
from bankai.orchestrator.session.store import SessionStore
from bankai.shared.config import Settings, get_settings
from bankai.shared.logging import configure_logging, get_logger

log = get_logger("bankai.app")


def build_llm_clients(s: Settings) -> dict[str, LLMClient]:
    clients: dict[str, LLMClient] = {}
    if s.anthropic_api_key.get_secret_value():
        clients["anthropic"] = AnthropicClient(s.anthropic_api_key.get_secret_value(), s.anthropic_base_url,
                                               s.llm_timeout_seconds)
    if s.self_hosted_base_url:
        clients["self_hosted"] = OpenAICompatClient(s.self_hosted_base_url, s.self_hosted_api_key.get_secret_value(),
                                                    s.llm_timeout_seconds)
    if "simulator" in (s.llm_primary, s.llm_fallback):
        if s.is_production_like:
            raise RuntimeError("the simulator LLM cannot be used in staging/prod")
        clients["simulator"] = SimulatorLLM()
    return clients


def create_app(
    settings: Settings | None = None,
    *,
    redis: Redis | None = None,
    mcp_targets: dict[str, ServerTarget] | None = None,
    llm_clients: dict[str, LLMClient] | None = None,
    ledger: Ledger | None = None,
) -> FastAPI:
    s = settings or get_settings()
    configure_logging(s.log_level, s.service_name)

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # noqa: ANN202
        r = redis or Redis.from_url(s.redis_url, socket_timeout=5, health_check_interval=30)
        led = ledger or Ledger.from_url(s.database_url)
        if not s.is_production_like:
            await led.create_schema()
        store = SessionStore(r, s)
        redactor = PiiRedactor(store)
        cost = CostTracker(r, s, led)
        router = LLMRouter(llm_clients or build_llm_clients(s), s, on_usage=cost.record)
        gateway = MCPGateway(mcp_targets or {"accounts": s.mcp_accounts_url, "transactions": s.mcp_transactions_url,
                                             "service": s.mcp_service_url}, s, ledger=led)
        coordinator = Coordinator(router, gateway, s)
        app.state.settings = s
        app.state.redis = r
        app.state.store = store
        app.state.redactor = redactor
        app.state.cost = cost
        app.state.ledger = led
        app.state.verifier = TokenVerifier(s)
        app.state.ratelimiter = RateLimiter(r, s.user_rate_limit_per_minute)
        app.state.turns = TurnService(s, store, redactor, coordinator, gateway, cost, led)
        app.state.background_tasks = set()
        log.info("startup", env=s.env.value, llm_chain=router.chain_for("accounts"),
                 self_hosted_only=s.self_hosted_only_agents)
        yield
        for t in list(app.state.background_tasks):
            await t
        if redis is None:
            await r.aclose()
        if ledger is None:
            await led.dispose()

    app = FastAPI(title="Bank Assistant API", version="1.0.0", lifespan=lifespan,
                  docs_url=None if s.is_production_like else "/docs", redoc_url=None, openapi_url=None
                  if s.is_production_like else "/openapi.json")
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_credentials=False,
                       allow_methods=["GET", "POST", "DELETE"], allow_headers=["Authorization", "Content-Type",
                                                                                 "X-Request-ID"])

    @app.middleware("http")
    async def request_context(request: Request, call_next):  # noqa: ANN001, ANN202
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        structlog.contextvars.bind_contextvars(http_request_id=rid)
        response = await call_next(request)
        response.headers.update({
            "X-Request-ID": rid,
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Cache-Control": response.headers.get("Cache-Control", "no-store"),
        })
        structlog.contextvars.unbind_contextvars("http_request_id")
        return response

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_error", error=type(exc).__name__)
        return JSONResponse({"detail": {"code": "internal_error", "message": "Internal error"}}, status_code=500)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    async def readyz(request: Request) -> Any:
        try:
            await request.app.state.redis.ping()
        except Exception:
            return JSONResponse({"status": "redis_unavailable"}, status_code=503)
        return {"status": "ready"}

    app.include_router(api_router)
    if s.metrics_enabled:
        app.mount("/metrics", make_asgi_app())
    setup_tracing(app, s)
    return app


def run() -> None:
    import os

    import uvicorn

    uvicorn.run("bankai.orchestrator.main:create_app", factory=True, host="0.0.0.0",  # noqa: S104  # nosec B104 - containers listen on all interfaces
                port=int(os.environ.get("PORT", "8000")), proxy_headers=True, forwarded_allow_ips="*")


if __name__ == "__main__":
    run()
