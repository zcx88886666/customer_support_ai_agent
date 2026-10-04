# Parent-graph language preference — 2026-10-03

The parent chat route now reads the authenticated customer's confirmed `language` preference through the guarded [memory adapter](../../apps/api/resolveai/memory.py) before running a request. Only the exact values `English`, `en`, or `en-US` (case insensitive) select English; all other values use the existing Chinese templates. The language is passed to the parent coordinator for the final answer and used for clarification, return-submission, and handoff messages. Specialist tasks receive the same minimal policy/order scope as before, and no preference is written into the graph checkpoint. The preference changes response wording only; policy evidence, order ownership, return confirmation, and refund approval still use their existing authority paths.

Two new Python tests checked an English composite answer with the same order and policy source versions, correction back to Chinese, revocation, no implied return confirmation, and a second customer's unchanged language. The full suite passed **68/68**, and the no-key `smoke-v2` runner passed **28/28 executions** across 25 synthetic cases (`evals/reports/20261004T011440Z-5356f4/`, ignored). The first test run failed because the second customer's demo package is delivered; the assertion was corrected to check Chinese wording instead of an undelivered-package warning.

The rebuilt Docker API then passed the [real-OIDC verification](../../scripts/verify_agent_language_oidc.py) against the synthetic demo accounts and PostgreSQL/PostgresStore: English parent answer with verified order/policy versions, another customer's Chinese answer and unchanged profile, Chinese answer after preference deletion, and restoration of the original profile. The verifier created fresh chat thread IDs and restored the original consent and language value in a `finally` block. It made three bounded synthetic chat requests; the API's configured cost-efficient specialist review model may have been called, but this check did not capture provider usage or cost. It did not issue a return or refund.

Reproduce after the Docker stack is healthy:

```bash
.venv/bin/pytest -q
.venv/bin/python evals/runners/run_smoke.py
docker compose --env-file .env -f infra/compose/compose.yaml up -d --build api
.venv/bin/python scripts/verify_agent_language_oidc.py
```

The preference allowlist intentionally does not interpret arbitrary language instructions or free-text `communication_style` as a model instruction. Broader style rendering, browser preference controls, and a dedicated write-load measurement remain open.
