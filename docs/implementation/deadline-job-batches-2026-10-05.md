# Pending deadline job batches, 2026-10-05

The queued deadline task now selects at most **100 pending alert candidates** in SQL. The query excludes issued returns and rows that already have the currently relevant due-soon or overdue alert. It orders candidates by receipt time and return ID; each row is then locked and rechecked before one alert/audit commits. Repeated and concurrent calls retain the existing composite-key protection. A prior due-soon alert does not hide the later overdue alert.

Direct operator calls keep their existing unlimited pending-candidate behavior. Their optional `batch_size` accepts only integers1–1000; booleans, fractional values and out-of-range inputs reject before SQL. Queued messages still contain only the namespace and cannot supply time, money or batch overrides.

Eight regressions went red→green: two-row batches advanced through seven overdue receipts as **2,2,2,1,0**, fourteen due-soon/overdue records remained unique across transitions, already-alerted histories required only the candidate query, and invalid limits rejected. A fixture consistency regression also exposed the existing job probe's receipt timestamps preceding their return creation. The probe now builds causal historical delivery, return and receipt facts rather than moving a current receipt backward.

The extended real Redis/PostgreSQL verifier adds 205 historical overdue receipts and checks scheduled task progress **100,100,5,0**, while retaining its approval/duplicate/namespace/worker-restart/broker-outage safety checks. Final run `20261005T075645Z-celery-75ebfd` passed **18/18 checks**, with exact **100,100,5,0** new-alert progress,207 unique alerts/audits, two authorized refund ledgers/audits, zero chronological violations and successful owned Redis/PostgreSQL resource removal. The deployed worker responded with one pong and seven OIDC/API/MCP checks passed. Fresh review found one Important timezone regression: SQLite discards offsets from bound threshold parameters, and DST wall-time arithmetic can move elapsed-day boundaries. Six equivalent-instant cases cover negative/positive offsets and a DST fallback in direct and bounded calls; four failed before UTC normalization and all six passed after. Warning and overdue transitions, just-before-boundary exclusion and UTC retries retain exactly two alerts/audits with no refund. No Critical or Minor finding was identified. The reviewer declined wider history performance, production delivery SLA, independent infrastructure reruns and the unrelated approved Langfuse readback. Refund scanning remains unchanged: applying a naive limit could starve valid refunds behind unresolved invalid proposals.

```bash
.venv/bin/python -m pytest tests/test_deadline_batches.py -q
.venv/bin/python scripts/verify_celery_jobs.py
```

These are synthetic development checks. They establish bounded candidate selection and progress, without a large-scale delivery SLA or a locked release claim.

Full Python suite: `PROMPT_RELEASE=specialists-dev-v1 .venv/bin/python -m pytest -q` passed **244 tests**, with four opt-in Docker/PostgreSQL skips, in24.23seconds. The deadline/job/cancellation focus passed30tests with one opt-in Docker skip.
