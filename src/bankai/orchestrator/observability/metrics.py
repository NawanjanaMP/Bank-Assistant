"""Prometheus metrics (Observability: prompts / agent calls / tool calls).

Infrastructure metrics (CPU, memory, disk) come from node-exporter / cAdvisor / kubelet,
scraped by the same Prometheus - see deploy/prometheus.
Label values are bounded enums (agent, tool, provider ...): never user data.
"""

from prometheus_client import Counter, Gauge, Histogram

TURNS = Counter("bankai_turns_total", "Chat turns", ["outcome"])
TURN_LATENCY = Histogram("bankai_turn_latency_seconds", "End-to-end turn latency",
                         buckets=(0.25, 0.5, 1, 2, 4, 8, 15, 30, 60))
TIME_TO_FIRST_TOKEN = Histogram("bankai_time_to_first_token_seconds", "Latency until first streamed text",
                                buckets=(0.1, 0.25, 0.5, 1, 2, 4, 8))
AGENT_INVOCATIONS = Counter("bankai_agent_invocations_total", "Agent invocations", ["agent", "outcome"])
AGENT_STEPS = Histogram("bankai_agent_steps", "LLM steps per agent run", ["agent"], buckets=(1, 2, 3, 4, 6, 8))
LLM_CALLS = Counter("bankai_llm_calls_total", "LLM calls", ["provider", "model", "agent"])
LLM_TOKENS = Counter("bankai_llm_tokens_total", "LLM tokens", ["provider", "model", "agent", "kind"])
LLM_COST = Counter("bankai_llm_cost_usd_total", "Estimated LLM spend (USD)", ["provider", "model", "agent"])
LLM_LATENCY = Histogram("bankai_llm_latency_seconds", "LLM call latency", ["provider", "agent"],
                        buckets=(0.25, 0.5, 1, 2, 4, 8, 16, 32))
TOOL_CALLS = Counter("bankai_tool_calls_total", "Tool calls from agents", ["agent", "tool", "outcome"])
TOOL_LATENCY = Histogram("bankai_tool_latency_seconds", "Tool call latency (orchestrator view)", ["tool"])
GUARDRAIL_EVENTS = Counter("bankai_guardrail_events_total", "Guardrail interventions", ["type"])
PII_ENTITIES = Counter("bankai_pii_entities_redacted_total", "PII entities tokenised before LLM", ["source"])
CONFIRMATIONS = Counter("bankai_confirmations_total", "Write-action confirmations", ["tool", "outcome"])
ACTIVE_STREAMS = Gauge("bankai_active_streams", "Open SSE streams")
