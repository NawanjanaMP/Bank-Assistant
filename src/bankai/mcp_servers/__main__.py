"""Run one MCP server over Streamable HTTP: ``python -m bankai.mcp_servers accounts``."""

from __future__ import annotations

import argparse
import os

import uvicorn
from prometheus_client import make_asgi_app
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from bankai.core_banking.client import HttpCoreBanking
from bankai.mcp_servers import accounts, service, transactions
from bankai.shared.config import get_settings
from bankai.shared.logging import configure_logging

BUILDERS = {"accounts": accounts.build, "transactions": transactions.build, "service": service.build}
DEFAULT_PORTS = {"accounts": 9101, "transactions": 9102, "service": 9103}


def create_app(name: str) -> Starlette:
    settings = get_settings()
    core = HttpCoreBanking(settings.core_banking_url, settings.core_banking_api_key.get_secret_value())
    server = BUILDERS[name](core, settings)
    # Stateless + JSON responses: any replica can serve any call, so the servers scale horizontally.
    mcp_app = server.streamable_http_app(stateless_http=True, json_response=True, host="0.0.0.0")  # noqa: S104  # nosec B104 - containers listen on all interfaces

    async def health(_):  # noqa: ANN001
        return JSONResponse({"status": "ok", "server": name})

    app = Starlette(
        routes=[Route("/healthz", health), Mount("/metrics", make_asgi_app()), Mount("/", mcp_app)],
        lifespan=lambda _app: mcp_app.router.lifespan_context(_app),
    )
    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("server", choices=sorted(BUILDERS))
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()
    settings = get_settings()
    configure_logging(settings.log_level, f"bankai-mcp-{args.server}")
    port = args.port or int(os.environ.get("PORT", DEFAULT_PORTS[args.server]))
    uvicorn.run(create_app(args.server), host="0.0.0.0", port=port, proxy_headers=True)  # noqa: S104  # nosec B104 - containers listen on all interfaces


if __name__ == "__main__":
    main()
