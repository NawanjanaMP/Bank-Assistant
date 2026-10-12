# Bank Assistant

A production-grade, multi-agent conversational banking assistant. A **coordinator agent** understands what the customer wants, checks **authorisation**, and hands the work to **specialist agents** (Accounts, Transactions, Service). Specialists reach bank systems only through **MCP servers**, which call core banking on the customer's behalf. Every model call passes through **PII redaction**; every change at the bank needs the customer's explicit **confirmation**; every call is **observed and costed**.

```
Browser (React)  ──►  Edge (WAF · rate limits · API gateway)  ──►  Orchestrator API  ◄──►  Bank IdP (OIDC)
                                                                     │
                     ┌────────────── Coordinator agent ──────────────┤── Authorisation (policy in code)
                     │          │                 │                  │── PII redaction (tokenise / restore)
               Accounts    Transactions        Service               │── LLM router: Claude ⇄ self-hosted
                agent         agent             agent                │── Session store (Redis)
                  │             │                 │                  │── Cost tracker · Observability
            Accounts MCP   Transactions MCP   Service MCP            └── Agent evaluation suite (CI)
                  └─────────────┴───── core banking ┘
```

## Run it

**Without Docker, no API key (fastest):** needs Python 3.12+, Node 20+ and `redis-server`.

```bash
make install
redis-server --daemonize yes
make dev            # http://localhost:5173 - pick a demo customer
```

With no `BANKAI_ANTHROPIC_API_KEY` the stack uses the built-in **simulator** LLM: a deterministic stand-in that drives the real pipeline (routing, MCP, authorisation, PII, confirmations, streaming, costing). Export a key to talk to Claude instead.

**Full stack in Docker** (edge/WAF, Keycloak, Postgres, Redis, three MCP servers, mock core banking, Prometheus, Grafana, Jaeger):

```bash
cp .env.example .env      # set BANKAI_ANTHROPIC_API_KEY, BANKAI_PII_VAULT_KEY, signing key
make up                   # app: http://localhost:8443  (users nimal / ayesha, see deploy/keycloak)
docker compose --profile self-hosted up -d ollama && docker compose exec ollama ollama pull qwen2.5:14b-instruct
```

**Checks:** `make test` (39 tests) · `make eval` (agent evaluation gate) · `make lint typecheck security`.

## Where each box in the design lives

| Diagram component | Implementation |
|---|---|
| User Interface (chat) | `web/` – React + TypeScript, OIDC PKCE login, streaming answers, agent routing thread, result cards, confirmation slips, dark mode, mobile |
| Edge Layer (WAF, DDoS, rate limits, API gateway) | `deploy/edge/` – NGINX + ModSecurity OWASP CRS, per-IP limits, SSE routing; volumetric DDoS at the CDN/cloud edge. K8s: ingress annotations in the Helm chart |
| Authentication – bank's identity provider | `orchestrator/security/oidc.py` (JWKS validation, audience, azp, customer-id claim, step-up via `auth_time`/`acr`); `deploy/keycloak/realm-bank.json` |
| API | `orchestrator/api/routes.py`, `orchestrator/main.py` – sessions, SSE chat turns, confirm/decline |
| Coordinator Agent | `orchestrator/agents/coordinator.py` + `prompts/coordinator.md` |
| Authorisation | `orchestrator/security/authz.py` + `shared/policy.py` (one policy shared with every MCP server, deny by default) |
| Accounts / Transaction / Service Agents | `orchestrator/agents/specialists.py`, `agents/base.py` (bounded tool loop), `prompts/*.md` |
| Accounts / Transactions / Service MCP Servers | `mcp_servers/` – Streamable HTTP, stateless, independent token verification, ownership checks, idempotent writes |
| Balance enquiry, transaction details, statement request, change of address, cheque book, KYC update | MCP tools over `core_banking/client.py` (`HttpCoreBanking` for the real integration layer, mock service for dev) |
| PII Redaction | `orchestrator/guardrails/pii.py` – reversible, session-scoped tokens, encrypted vault, checksum-validated detectors |
| Self-hosted LLM / Third-party LLM | `orchestrator/llm/` – Claude adapter, OpenAI-compatible adapter (vLLM/Ollama/TGI), router with failover, circuit breaker, data-residency pinning |
| Session Store (history, inter-agent state) | `orchestrator/session/store.py` – Redis; tokenised history, shared state, PII vault, pending actions, turn lock |
| Observability (prompts, agent calls, tool calls; CPU, memory, disk) | `orchestrator/observability/` + `deploy/prometheus`, `deploy/grafana`, `deploy/otel` |
| Cost Tracker | `orchestrator/observability/cost.py` + `db.py` usage ledger; per-session and per-customer daily budgets |
| Agent Evaluation Suite | `src/bankai/evals/` – golden dataset, routing/tool/safety/privacy/answer scoring, optional LLM judge, CI gate |

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the request lifecycle, security model and production checklist.

## Repository layout

```
src/bankai/
  shared/          config, signed user-context tokens, shared tool policy, logging
  orchestrator/    API, coordinator + specialists, LLM router, MCP gateway, guardrails, session, observability
  mcp_servers/     accounts, transactions, service MCP servers
  core_banking/    core banking adapter (HTTP + in-memory) and mock service
  evals/           agent evaluation suite + golden dataset
web/               React UI
deploy/            edge/WAF, Keycloak realm, Prometheus/Grafana/OTel, Helm chart
migrations/        Alembic (usage ledger, append-only audit trail)
tests/             security, LLM routing and end-to-end tests
```
