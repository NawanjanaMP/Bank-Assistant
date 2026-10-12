#!/usr/bin/env bash
# Local development without Docker: mock core banking, three MCP servers, orchestrator, web.
# Uses dev auth and the simulator LLM unless BANKAI_ANTHROPIC_API_KEY is set.
set -euo pipefail
cd "$(dirname "$0")/.."

export PYTHONPATH=src
export BANKAI_ENV=local
export BANKAI_AUTH_MODE=${BANKAI_AUTH_MODE:-dev}
export BANKAI_CONTEXT_TOKEN_SIGNING_KEY=${BANKAI_CONTEXT_TOKEN_SIGNING_KEY:-local-dev-signing-key-0123456789abcdef}
export BANKAI_DATABASE_URL=${BANKAI_DATABASE_URL:-sqlite+aiosqlite:///./dev.db}
if [[ -z "${BANKAI_ANTHROPIC_API_KEY:-}" ]]; then
  export BANKAI_LLM_PRIMARY=simulator BANKAI_LLM_FALLBACK= BANKAI_SELF_HOSTED_BASE_URL=
  echo "No BANKAI_ANTHROPIC_API_KEY: using the offline simulator LLM."
fi

pids=()
trap 'kill "${pids[@]}" 2>/dev/null' EXIT
python -m bankai.core_banking.mock_service & pids+=($!)
for s in accounts transactions service; do python -m bankai.mcp_servers "$s" & pids+=($!); done
sleep 2
python -m bankai.orchestrator.main & pids+=($!)
(cd web && VITE_AUTH_MODE=$BANKAI_AUTH_MODE npx vite --port 5173) & pids+=($!)
echo "Open http://localhost:5173"
wait
