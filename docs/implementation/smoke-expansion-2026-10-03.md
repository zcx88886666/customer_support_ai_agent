# Synthetic smoke suite expansion — 2026-10-03

Five author-written development cases were added to `smoke_demo.jsonl`: an excluded product return, an expired return window, a quantity above purchased units, a foreign customer's attempted return, and a delivered-order question requiring both logistics and policy evidence. The runner now requires **30 unique case IDs**. Three pre-existing collaboration-tagged cases still execute in both single and collaborative modes, giving **33 total executions**.

The no-key HTTP replay created a fresh SQLite world per execution and passed **33/33**, with zero critical failures; report `evals/reports/20261004T023154Z-394702/` is ignored by Git. The full Python suite passed **72/72**. The new cases assert concrete error codes or verified specialist sources, plus zero return and refund ledger rows through the database-first scorer.

All thirty cases were authored within this repository. At the time of this report, eleven older rows still carried `split: locked` labels; those labels did **not** mean independent human gold review or a sealed benchmark. On 2026-10-04, they were corrected to `dev`, and the smoke loader began rejecting misleading split labels. The intended independently reviewed evaluation program remains open.

```bash
.venv/bin/python evals/runners/run_smoke.py
.venv/bin/pytest -q
```
