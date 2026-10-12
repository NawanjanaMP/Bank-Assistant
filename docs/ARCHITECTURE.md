# Architecture

## 1. A chat turn, end to end

1. **Edge.** The WAF (OWASP CRS) inspects the request, per-IP limits apply, and the gateway routes `POST /v1/sessions/{id}/messages` to the orchestrator with buffering off (Server-Sent Events).
2. **Authentication.** The orchestrator validates the bank IdP's access token against its JWKS: signature, issuer, audience, expiry, authorised client, and the `customer_id` claim. The session must belong to this subject, and a per-customer rate limit (Redis) applies.
3. **Budget and hygiene.** The cost tracker refuses the turn if the session or the customer's daily spend is exhausted. Input is Unicode-normalised, control and zero-width characters are stripped, and the length is capped.
4. **Injection screen.** High-confidence jailbreak patterns are refused before any model call. Medium scores are logged and counted.
5. **PII redaction.** Card numbers (Luhn-checked), account numbers, NICs, phones, emails and IBANs become session-stable placeholders (`[CARD_1]`). Originals go to the session vault, Fernet-encrypted in Redis.
6. **Coordinator** (fast, cheap model). It sees tokenised history and calls `delegate_to_<agent>` tools, or answers small talk itself.
7. **Authorisation.** Before each delegation, code (not the model) checks that the customer holds the agent's scopes.
8. **Specialist agent** (stronger model). It runs a bounded tool loop (`agent_max_steps`) over its explicit tool allow-list and streams its answer straight to the UI, labelled with its name.
9. **MCP gateway.** For every tool call: allow-list and scope check, restore placeholders in the model's arguments, mint a 60-second user-context JWT for that MCP server's audience, call it, tokenise PII in the result, and wrap the result as `untrusted-data` before the model sees it.
10. **MCP server.** It independently verifies the token, scope and ownership. It takes the customer id from the token, never from tool arguments, and calls core banking.
11. **Persist.** Tokenised user text and the final answer go to history. Usage goes to Redis counters, Prometheus and the Postgres ledger. The turn ends with `turn.completed`.

## 2. Changes at the bank: human in the loop

Write tools (`request_statement`, `change_address`, `request_cheque_book`, `update_kyc`) are **never executed from the model loop**. The gateway parks them as a pending action (10-minute TTL) and emits `confirmation.required`. The model is told nothing has happened yet.

When the customer presses **Confirm**:

* `step_up` tools (address, KYC) require a recent strong authentication (`auth_time` within 5 minutes and `acr` = `aal2`). Otherwise the API returns `401 step_up_required` and the UI re-authenticates with `max_age=0&acr_values=aal2`.
* The action is taken with an atomic `GETDEL`, so it can execute at most once.
* The orchestrator mints a token carrying `act = <tool>:sha256(arguments)`. The MCP server recomputes the fingerprint and rejects any mismatch, so a tampered or replayed call cannot pass.
* The action id is the idempotency key all the way to core banking.

## 3. LLM routing and data residency

| Concern | Mechanism |
|---|---|
| Model per role | Coordinator → `claude-haiku-4-5-20251001`, specialists → `claude-sonnet-5` (configurable) |
| Outage | Primary → fallback provider; per-provider circuit breaker; streams only fail over before the first token |
| Residency | `BANKAI_SELF_HOSTED_ONLY_AGENTS=["service"]` pins an agent to the self-hosted model, which **fails closed** (no third-party fallback) |
| Third-party exposure | Only tokenised content and an opaque session id are sent; prompt caching on the static system prompt cuts cost |

## 4. Defence in depth

| Threat | Controls |
|---|---|
| Account takeover / token theft | OIDC + PKCE, short access tokens in session storage, audience + azp checks, step-up for sensitive changes |
| Horizontal privilege escalation (another customer's data) | Customer id only from the verified token; session ownership checks; MCP servers re-verify; core enforces ownership and returns 404, never 403 |
| Prompt injection (direct) | Input screen, rule-locked system prompts, no identity or tools for the model to abuse |
| Prompt injection (indirect, merchant narratives) | Untrusted-data envelope, neutralisation of instruction-like spans, explicit rule in every prompt |
| Model performs an unwanted action | Write tools parked for confirmation; approval bound to exact arguments; idempotency; per-agent tool allow-lists |
| PII leakage to providers or logs | Reversible tokenisation before every model call; encrypted vault; log scrubber; OTel attribute scrubbing |
| Abuse and cost blow-ups | Edge per-IP limits, per-customer limits, session and daily spend budgets, step caps, max tokens |
| Lateral movement | Network policies (edge → orchestrator → MCP → core); non-root, read-only containers; secrets from Vault/KMS |
| Repudiation | Audit trail of every tool call, request, confirmation and decline (append-only grants), correlated by request id |

## 5. Observability

* **Metrics** (`/metrics`): turns, latency, time-to-first-token, agent invocations and steps, LLM calls, tokens and cost by provider/model/agent, tool calls and latency, guardrail events, confirmations funnel. Host CPU, memory and disk come from node-exporter and cAdvisor.
* **Traces** (OTLP → Jaeger/Tempo): `agent.run`, `llm.complete`, `mcp.tool` spans with token counts and no content.
* **Logs**: JSON with request, session and subject ids, and a PII scrubber.
* **Dashboard and alerts**: `deploy/grafana`, `deploy/prometheus/alerts.yml` (error rate, TTFT, failover, spend spike, injection spike, tool denials, MCP errors, disk, CPU).

## 6. Evaluation

`bankai-eval` replays `evals/datasets/golden.jsonl` through the real pipeline and scores routing accuracy, tool recall, safety (no write without confirmation), guardrails, privacy (inspects every model request for raw PII) and answer checks. The run fails when a threshold is missed.

* **Every PR:** simulator provider, a deterministic regression gate for the pipeline.
* **Nightly, and on PRs labelled `prompt-change`:** `--provider anthropic --judge` adds an LLM-as-judge score for helpfulness, groundedness and safety.
* **Before routing traffic to a new self-hosted model:** `--provider self_hosted`.

Grow the dataset from production conversations: sampled, redacted, labelled.

## 7. Scaling

The orchestrator and MCP servers are stateless; all state lives in Redis and Postgres. Each scales horizontally (HPA in the Helm chart). MCP servers run Streamable HTTP in stateless JSON mode, so any replica serves any call. Use Redis with replication or Sentinel (or a managed service), and Postgres with a standby.

## 8. Before go-live checklist

- [ ] Replace the mock core with the bank integration layer (`HttpCoreBanking` base URL, mTLS, credentials from Vault).
- [ ] Point OIDC at the production IdP; configure the `customer_id` mapper, banking scopes, MFA policy and `acr` mapping.
- [ ] Move context tokens to ES256 with a KMS-held key; MCP servers get only the public key.
- [ ] Generate and rotate `BANKAI_PII_VAULT_KEY`; consider Presidio NER for names and free-text addresses (`extra_detectors`).
- [ ] Agree data residency with risk and compliance; set `SELF_HOSTED_ONLY_AGENTS`, and the provider's data retention and region settings.
- [ ] Run the WAF in DetectionOnly against real traffic, tune it, then switch to blocking. Put CDN DDoS protection in front.
- [ ] Ship audit events to the SIEM with WORM retention; agree retention for history (Redis TTL) and the ledger.
- [ ] Set alert routing (PagerDuty/Opsgenie) and SLOs; load-test the SSE path and LLM rate limits.
- [ ] Complete model risk review with eval results, red-team the injection and authorisation paths, and pen-test the app.
- [ ] Have legal and compliance review customer-facing copy (disclaimers, complaint routes, fraud hotline numbers).
