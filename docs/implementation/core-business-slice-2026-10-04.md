# First conversational core-business development slice

On 2026-10-04, `core_business_dev_v1.jsonl` gained one **author-written development case**. The customer Agent creates a confirmed return through `/chat`; the cross-role replay then receives, inspects, proposes, approves, and issues the simulated refund on the same isolated SQLite database. Its direct `/returns` call is an idempotency replay of the Agent-created request. The local scorer checks the pre-approval zero-ledger state, final owned return, one authorized ledger, amount, and audit actions. The case has `review.status: pending`; it is not locked gold.

The first run (`20261004T105546Z-core-81e913`) was incomplete: the replay reused the chat's idempotency key with a different reason and got `idempotency_conflict`. The runner now takes the fixture reason for both replay calls. The successful SQLite report is ignored `evals/reports/20261004T105755Z-core-83ea92/`: **1/1 pass**, all 21 checks true, `development_pass=true`, `v6_minimum_cases_met=false`, and `release_gate_pass=false`. The run uses one recorded seed timestamp; the application wall clock still advances.

The same Agent-to-terminal case then passed **21/21 checks** with real Keycloak authorization-code tokens, fresh migrated PostgreSQL, and separate worker processes in ignored `evals/reports/20261004T110247Z-oidc-189d91/`. The existing direct-API approved-refund case passed again after the new `--core` path was added, in `evals/reports/20261004T110306Z-oidc-93ec60/`. Both runs were development checks; the core report keeps `gate_pass=false` and `release_gate_pass=false`.

Commands and results:

```text
.venv/bin/python evals/runners/run_core_business.py  1/1 pass
.venv/bin/python evals/runners/run_business.py       6/6 pass
.venv/bin/pytest -q                                  84 passed
git diff --check                                      passed
BUSINESS_PG_ADMIN_URL=... run_business_oidc_postgres.py --core  1/1 pass, 21/21 checks
BUSINESS_PG_ADMIN_URL=... run_business_oidc_postgres.py --case-id business-approved-refund  1/1 pass
```

Next: add distinct scenario families and independent review before claiming the 30-case v6 minimum. The OIDC runner still lacks the full v6 HTML/report contract and a frozen fixture clock.
