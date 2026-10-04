# Shared request resources and external transport verification, 2026-10-04

## Implemented controls

[`RequestBudget`](../../apps/api/resolveai/request_budget.py) creates one request-local, locked resource account for each chat turn. Intent calls, both parallel specialist branches, technical retries, and Replan iterations share it; no budget object or credential enters a graph checkpoint. Resource usage is returned as `resource_usage` and recorded on an OTel span.

Defaults are 25 seconds for external-work admission/cancellation, at most **10 LLM attempts**, 16,000 accounted tokens, and $0.02 accounted cost per turn. Environment settings can lower the call cap; values above ten or nonpositive limits fail API startup. [`resources-v1.json`](../../packages/agent/resources-v1.json) records per-call admission bounds, two technical attempts, disabled provider fallback, and pinned-model price estimates. The configured GPT-4o-mini prices match [OpenRouter's model listing](https://openrouter.ai/openai/gpt-4o-mini-2024-07-18), checked 2026-10-04. Generation spans include the exact schema and resource-policy hashes alongside the local Prompt hash.

Each attempt reserves a conservative byte-based input estimate, maximum output tokens, and estimated cost before dispatch. Valid provider usage replaces the reservation; failed or cancelled requests retain unknown-use reservations. Reported overruns stop further calls and cause a safe handoff. Invalid structured answers are charged and never trigger technical retries. These are **admission guards**, not a guarantee of the provider's eventual bill: unknown usage, tokenizer framing, prices, or an in-flight provider charge can differ from estimates.

OpenRouter I/O now uses an asynchronous HTTP client inside an AnyIO cancellation deadline covering the entire response. The timeout is bounded by both the request and specialist deadline. This also stops a peer that keeps sending bytes within individual HTTP read timeouts. Commerce MCP uses the same remaining deadline, retaining its existing bounded read-only tool list and signed-token authorization. Deadline/resource exhaustion cannot authorize a return or refund.

Authentication precedes this budget, and synchronous database/checkpoint operations are not forcibly interrupted. A return write checks admission before starting; a successfully completed return remains reported as submitted even if database work takes longer. A strict wall-clock limit for SQL lock waits and transaction execution remains separate work.

## Actual transport results

[`verify_specialist_transport.py`](../../scripts/verify_specialist_transport.py) creates a fresh migrated PostgreSQL database per execution, builds its policy index, and starts disposable API and Commerce MCP processes. It obtains a real Keycloak authorization-code/PKCE customer token. Model traffic goes only to a local HTTP stub with a synthetic key; **no OpenRouter credit is spent by this verifier**.

Final report: `evals/reports/20261004T233606Z-transport-eefabf/`, **6/6 cases and 60/60 checks**, zero incomplete cases. Each fault runs in both `single` and `collab` modes:

| Fault | Measured outcome |
|---|---|
| Model response sends two bytes every 40 ms | The 0.8-second external-work deadline stopped the response and handed off safely. One unknown-use attempt was accounted, with no pending call. A new turn recovered the owned shipment with a fresh budget. |
| Authenticated shipment tool sleeps 12 seconds | The client timed out at its eight-second tool bound, answered only from verified policy, and acknowledged the missing order evidence. The server's late completion did not replace the recovered thread result. |
| Authenticated tool sends a delivered snapshot conflicting with SQL | The order finding was discarded, the verified policy remained usable, and no false delivery claim appeared. A subsequent correct snapshot recovered normally. |

All six databases had zero return requests and refund ledger entries. The final MCP logs had zero internal transport errors. Reports contain manifest, per-case JSONL, summary, HTML, migration/API/MCP logs, and provider event logs; signed tokens, passwords, and keys are not stored in them. Databases remain locally for inspection. These are authored development fault checks, not locked customer benchmarks or a production service-level claim.

## Failures preserved and resolved

- The first invocation could not import the repository's `evals` package when run as a script. The verifier now sets its repository import root.
- `20261004T233059Z-transport-772f69` passed 2/6: the fresh database had no policy search index, so expected verified-policy assertions failed. The setup now indexes the seeded bundle. The indexed rerun `20261004T233300Z-transport-120f27` passed 6/6 safety cases.
- Those stateful MCP runs exposed an SDK session-cancellation race: `ClosedResourceError` followed by an ASGI response-after-completion error. A stateless JSON transport trial, `20261004T233451Z-transport-dde3b3`, passed both slow-tool cases without internal errors. Commerce MCP now uses that configuration, and the final six-case run verifies the production configuration. JWT and object-level authorization remain active on each request.

## Regression results and reproduction

The seven-suite no-key minimum run `20261004T233610Z-minimum-dacf9a` passed **7/7**, including **147 Python tests**. The unchanged 11-case live paired development suite passed **22/22 executions** through the new asynchronous provider transport: **40 calls, 8,728 input tokens, 1,138 output tokens, $0.00199200** provider-reported cost, and no missing cost records. Report: `evals/reports/20261004T233305Z-paired-model-cdb3e2`. All eleven pairs tied on task success; no collaboration advantage is claimed. The usage meter now captures and restores both synchronous and asynchronous HTTP client methods.

```bash
export SPECIALIST_TRANSPORT_PG_ADMIN_URL='postgresql://resolveai@<isolated-postgres-address>:5432/postgres'
.venv/bin/python scripts/verify_specialist_transport.py
.venv/bin/python scripts/verify_minimum.py
```

The transport verifier requires the local Keycloak demo accounts prepared by the README workflow. `--scenario llm_trickle|mcp_slow|mcp_contradiction` selects a two-mode subset. The locked release gate remains false: human review, a leakage-safe held-out split, full model tool selection/synthesis, and other v6 items remain open.

## Local Docker deployment

`docker compose --env-file .env -f infra/compose/compose.yaml up -d --build api mcp` rebuilt and started the local API and MCP services. The API health check passed. `.venv/bin/python scripts/verify_oidc.py` then passed all seven named real-Keycloak/API/MCP authorization checks, including authenticated agent reads and cross-customer/role denials. Existing credentials remain in ignored local configuration.
