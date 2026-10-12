.PHONY: install dev test eval eval-model lint typecheck security web up down logs

install:          ## install Python (editable, with dev tools) and web dependencies
	pip install -e ".[dev]"
	cd web && npm install

test:             ## unit + end-to-end tests (in-process MCP servers, simulator LLM)
	pytest -q

eval:             ## agent evaluation suite, pipeline regression gate (no API key needed)
	bankai-eval --out eval-report.json

eval-model:       ## agent evaluation against the real model, with LLM-as-judge
	bankai-eval --provider anthropic --judge --out eval-report-anthropic.json

lint:
	ruff check src tests
	cd web && npm run typecheck

typecheck:
	mypy src

security:         ## static analysis + dependency audit
	bandit -q -r src -x tests
	pip-audit

dev:              ## run everything locally without Docker (needs redis-server on :6379)
	./scripts/dev.sh

up:               ## full stack in Docker: edge/WAF, Keycloak, API, MCP servers, observability
	test -f .env || cp .env.example .env
	docker compose up --build -d
	@echo "App: http://localhost:8443  Keycloak: http://localhost:8080  Grafana: http://localhost:3000  Jaeger: http://localhost:16686"

down:
	docker compose down

logs:
	docker compose logs -f orchestrator mcp-accounts mcp-transactions mcp-service
