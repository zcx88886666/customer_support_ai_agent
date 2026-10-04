# Business workflow v2 and targeted mutation measurement — 2026-10-03

The [v2 development dataset](../../evals/datasets/business_workflows_v2.jsonl) preserves the four v1 cross-role stories and adds explicit unconfirmed-return and expired-window denials. The [business runner](../../evals/runners/run_business.py) replays each through isolated SQLite, mock-auth HTTP endpoints, and the controlled worker, then scores database terminal state and audit. The first v2 run exposed a scorer assumption: it demanded a return ID for a correctly denied request. The scorer now requires that ID only when gold expects a persisted return. The rerun passed **6/6 cases**, with no incomplete results, under ignored `evals/reports/20261004T003803Z-business-a65763/`.

The [targeted mutation runner](../../evals/runners/run_business_mutations.py) patches actual application functions for one isolated case per fault and then applies the same v2 terminal-state scorer. Its fixed nine mutants and observed detection checks were:

| Deliberate application fault | Representative detection |
|---|---|
| Worker never issues an approved refund | Ledger count, proposal/return state, audit |
| Foreign customer is accepted for a return | HTTP status, return owner, terminal state |
| Paid refund is one cent too low | Independent amount check on the persisted ledger |
| Stale proposal is approved | Expected refusal and regenerated proposal |
| Refund issuance audit is omitted | Audit count and ledger authorization |
| Return creation audit is omitted | Audit count |
| Failed warehouse inspection is treated as passing | Proposal refusal and terminal state |
| Explicit customer confirmation is ignored | Expected denial and zero returns |
| Expired seven-day window is ignored | Expected denial and zero returns |

All **9/9 executable mutants were killed**, none survived or crashed, for a **100% score on this named mutation set**. The local manifest, mutation outcomes, and summary are under ignored `evals/reports/20261004T003939Z-business-mutations-97fca5/`. The one-cent under-refund reached the ledger and failed specifically on the independent amount check. The full Python suite passed **66 tests**. This measurement is deliberately scoped; it does not imply 100% detection of arbitrary bugs or replace a larger, human-reviewed locked suite. It uses mock authentication and SQLite, while separate OIDC/PostgreSQL/browser checks cover other slices.

Reproduce with:

```bash
AUTH_MODE=mock .venv/bin/python evals/runners/run_business.py
AUTH_MODE=mock .venv/bin/python evals/runners/run_business_mutations.py
.venv/bin/pytest -q tests/test_business_eval.py
```
