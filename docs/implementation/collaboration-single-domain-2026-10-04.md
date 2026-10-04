# Single-domain collaboration development check — 2026-10-04

`collaboration_single_dev_v1.jsonl` adds 20 author-written single-domain questions: ten owned order/shipment lookups and ten policy-only questions, across five template families. The paired runner uses the same isolated HTTP fixture, `smoke-v2` database-first scorer, random within-case execution order, and per-mode usage metrics as the composite suite. Gold requires exactly one specialist finding and no refund ledger. This checks the v6 requirement that single-domain questions do not fan out to both specialists.

The mock report `evals/reports/20261004T093726Z-collab-single-domain-mock-bf27ab/` passed **20/20 in each mode**, with zero dispatch or evidence errors. The pinned GPT-4o-mini report `evals/reports/20261004T093739Z-collab-single-domain-live-a598c2/` also passed **20/20 in each mode**. Each mode recorded 20 specialist tool calls and 40 provider calls. Single used 8,123 input and 868 output tokens, cost **$0.00173925**, with P50/P95 **2,909.56/3,973.62 ms**. Collab used 8,123 input and 850 output tokens, cost **$0.00172845**, with P50/P95 **2,892.90/4,455.93 ms**. These are provider-reported costs from one development run. Both modes tied on all twenty cases. The original composite mock suite still passed **40/40** after the runner gained the suite selector.

The prompts, labels, and seeded demo orders are synthetic development material. Some policy questions share wording and source clauses. The set has not been independently reviewed or split into a locked test group, so the pass rate is a regression result rather than a production accuracy estimate. The current `single` path executes the same read-only specialist logic sequentially, while `collab` fans out the tasks; this comparison does not test a separate model's reasoning quality.

The runner now captures one `seed_clock` for every database in a paired run. The post-change mock report `evals/reports/20261004T095852Z-collab-single-domain-mock-620cd5/` passed 40/40 and every case row matched the manifest clock. The earlier live run predates this change; its result remains a development measurement, but it did not use one exact seed timestamp. The application wall clock is not frozen.

```bash
.venv/bin/python evals/runners/run_collaboration.py --suite single_domain --mode mock
.venv/bin/python evals/runners/run_collaboration.py --suite single_domain --mode live
```
