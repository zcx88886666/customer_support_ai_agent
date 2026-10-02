# Langfuse smoke-run reconciliation — 2026-10-02

The existing 25 original synthetic smoke cases ran in isolated SQLite fixtures with no OpenRouter model key. All **28 executions passed**, including the three paired single/collaborative cases, with no critical failures. Langfuse US credentials were loaded only into the runner process from the ignored local `.env` and flushed after the run. The local run ID was `cloud-smoke-20261002-v1`; its complete report remains under ignored `evals/reports/cloud-smoke-20261002-v1/`.

The export script then queried Cloud observations and scores for the exact local trace IDs and stored only redacted trace identifiers, span hierarchy, run/case links, score names/values, and hashes under ignored `observability/exports/cloud-smoke-20261002-v1/`. Reconciliation measured:

| Item | Result |
|---|---:|
| Local executions and expected traces | 28 |
| Traces with Cloud observations | 28 |
| Traces with matching local `run_id` and `case_id` metadata | 28 |
| Matching `task_success` scores | 28 |
| Exported observations | 160 |
| Exported scores | 28 |
| Missing local trace IDs, observations, links, or scores | 0 |

The export manifest status was `complete`. The script now reports incomplete status when a local trace, Cloud observation, matching case link, or expected score is missing. It refreshes incomplete cached traces on retry and backs off on API rate limiting. Each exported trace file has a SHA-256 recorded in the manifest. This is a smoke evaluation reconciliation, not the larger reviewed or locked v6 evaluation. The observed 28 + 160 + 28 objects are useful for a first quota estimate; they are not a verified Langfuse billing figure.
