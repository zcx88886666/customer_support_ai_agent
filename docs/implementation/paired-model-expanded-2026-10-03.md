# Expanded cost-efficient paired model development run — 2026-10-03

The [paired runner](../../evals/runners/run_paired_model.py) now supports `--scope development`: three composite and eight single-domain cases from the versioned 25-case synthetic smoke fixture are run in both `single` and `collab` modes. Each execution gets a fresh SQLite fixture, the same pinned `openai/gpt-4o-mini-2024-07-18` model/provider configuration, local Prompt release, and the existing database-first `smoke-v2` scorer. A recorded seed randomizes which mode runs first within each pair. This is an expanded development check; fixture `split` labels do not make these cases human-reviewed locked gold.

The first 11-pair run (`evals/reports/20261004T014631Z-paired-model-88b4b7/`, ignored) passed 8/11 single and 9/11 collaborative. Five executions failed deterministic route/specialist checks: the model labeled two clear “can I return?” policy inquiries as `return_request`, and a shipment lookup received an `after_sales` route despite its read-only intent. No refund or return was issued in those failures. The [routing guard](../../apps/api/resolveai/agent.py) now treats explicit eligibility questions without submission or refund verbs as policy inquiries and normalizes read-only model intents to the `knowledge` route. A Python regression test covers both guards, confirms an explicit “I want to return” remains a submission, and verifies that mixed eligibility/refund wording reaches the high-risk handoff. The first mixed-wording assertion expected the wrong intent label; it was corrected to assert the existing refund handoff, then the test passed.

The identical 11-case paired rerun with seed `20261003` passed **11/11 in each mode, 22/22 executions**, with 11 ties and no wins for either mode. The ignored report is `evals/reports/20261004T014859Z-paired-model-36b1c6/`. Both runs used 40 provider calls; the passing run's provider-reported aggregate cost was **$0.0020136** (single $0.0009921, collaborative $0.0010215), with no missing cost records. Median measured request latencies were 2,959.03 ms single and 2,511.88 ms collaborative. These are sequential local observations on eleven authored cases, not evidence of a general latency or quality advantage. The model did not select tools or synthesize free-form answers; it routed intent and reviewed verified evidence.

The full Python suite then passed **71/71**, the no-key smoke suite passed **28/28 executions** across 25 cases, and the Docker API was rebuilt and returned healthy after the final high-risk guard adjustment. The rerun consumed only the configured cost-efficient pinned model; no high-cost model was used.

```bash
.venv/bin/python evals/runners/run_paired_model.py --scope development --seed 20261003
.venv/bin/pytest -q
.venv/bin/python evals/runners/run_smoke.py
```

The larger planned collaboration suite, two-person critical gold review, model-driven tool choice and synthesis, and paired locked evaluation remain open.
