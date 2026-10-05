# Duplicate dispatch convergence, 2026-10-05

The parent graph previously concatenated findings and could execute a repeated LangGraph Send twice. A real graph regression reproduced two order executions and two policy executions from one duplicated fanout. This violated v6's task-keyed convergence and could spend another specialist's tool/model allowance on the same task.

The parent now uses a deterministic finding reducer keyed by task ID and plan revision. Identical repeats merge once. Different envelopes for the same key yield one empty conflict marker, independent of arrival order; the existing bounded Replan route rechecks the affected evidence. A request-local `TaskRuns` registry binds each full task contract to one execution. Repeated callers await its Event within the request/task deadline, receive independent result copies, and share its error without rerunning tools. A changed contract under the same key is rejected as an empty conflict. Expired tasks cannot execute or retrieve cached success. The registry belongs to one coordinator instance, with no shared customer cache or durable business action.

Independent review passed **57 focused tests** and tested **24 conflict permutations** with identical convergence. It found a mixed naive/aware timestamp crash in schema-valid findings. The author treated that as a contract failure despite current producers emitting UTC: its regression failed with `TypeError`, then passed after normalizing query timestamps to UTC before comparison. The fix does not accept new facts or weaken source validation. Durable dispatch deduplication across process restarts was outside review; transaction/ledger idempotency remains the separate domain guarantee.

## Actual verification

- New module/graph tests: **8/8**. The injected duplicate Send test failed with two executions per specialist before integration and passed with one afterward. Cache copying, changed order reference collision, owner failure, bounded duplicate wait, expiry, different revisions, mixed timestamps and contradictory envelope reduction are covered.
- Focused graph/planning/module suite before the timestamp follow-up: **66/66**.
- Actual Keycloak/MCP/fresh-PostgreSQL duplicate probe `20261005T004325Z-transport-6a3b9b`: **2/2 cases, 22/22 checks**. Both sequential repeated tasks and parallel duplicated Sends produced one order read, one shipment read, four local model-stub calls total, and two unique findings. No return/refund was written; a fresh customer turn recovered normally.
- Full transport v3 `20261005T004543Z-transport-10af35`: **10/10 cases, 106/106 checks**, zero incomplete cases. The earlier eight trickling-model, slow/late MCP, contradiction and SQL-lock cases remain green.
- Final no-key minimum after timestamp normalization `20261005T004711Z-minimum-41d300`: **7/7 suites**, including **183 Python tests** and one opt-in PostgreSQL test skipped. No external model credit was used by these tests.
- Historical `release-v1` duplicate probe `20261005T005022Z-transport-03c866`: **2/2 cases**, zero failed checks; the old artifact used its expected two evidence-review calls while preserving once-per-task execution.
- The Docker API was rebuilt after timestamp normalization; all **seven real-OIDC authorization checks** passed.

Use the README's real Keycloak setup and local PostgreSQL admin connection:

```bash
PROMPT_RELEASE=specialists-dev-v1 \
SPECIALIST_TRANSPORT_PG_ADMIN_URL='postgresql://resolveai@<isolated-postgres-address>:5432/postgres' \
  .venv/bin/python scripts/verify_specialist_transport.py --scenario duplicate_dispatch
```

Omit `--scenario` for the full ten-case matrix. The original `release-v1` uses two evidence-review stub calls per fresh execution; the new development artifact additionally uses its two planning calls. Both enforce the same once-per-task execution rule. Cloud mirroring of the new artifact remains blocked by the separately recorded automatic approval review; this slice performs no external prompt export. Independent human gold and the locked release gate remain open.
