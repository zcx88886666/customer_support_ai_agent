# Small paired real-model comparison — 2026-10-03

The three original synthetic composite cases tagged `collaboration` in `smoke_demo.jsonl` were replayed once in `single` mode and once in `collab` mode, each against a fresh SQLite fixture with the same `release-v1` Prompt hashes and `smoke-v2` scorer. [The runner](../../evals/runners/run_paired_model.py) used the previously stored ignored OpenRouter key and the configured cost-efficient `openai/gpt-4o-mini-2024-07-18` model. It recorded only provider-reported token counts, cost, and HTTP status; it did not print or store the key, request body, or raw model response. The full local report is under ignored `evals/reports/20261004T000426Z-paired-model-395b79/`.

| Mode | Cases passed | Model calls | Input / output tokens | Provider-reported cost | Median case latency |
|---|---:|---:|---:|---:|---:|
| Single | 3/3 | 6 | 1,452 / 233 | $0.0003576 | 3,726.40 ms |
| Collaborative | 3/3 | 6 | 1,452 / 235 | $0.0003588 | 1,808.98 ms |

All three pairs tied on local case success: zero collaborative wins, zero single-mode wins, and zero incomplete results. All 12 provider responses included cost metadata; the combined reported cost was **$0.0007164**. The deterministic undelivered-parcel rule routed these questions, so the model calls were the two specialist evidence reviews per execution. The modes differed mainly in sequential versus parallel specialist scheduling. Three cases run once each are too few and too sensitive to network/cache order for a latency or quality improvement claim. This is a development connectivity/cost comparison; full model-driven synthesis, tool choice, and a larger human-reviewed locked paired set remain open.

Reproduce with `.venv/bin/python evals/runners/run_paired_model.py` after storing `OPENROUTER_API_KEY` in the ignored repository `.env` or setting it in the process environment. The runner writes a manifest, six case results, three pair results, summary JSON, and HTML under ignored `evals/reports/<run_id>/`. A no-network unit test verifies the provider usage meter filters to OpenRouter responses and restores the HTTP client method afterward. The full Python suite passed **59 tests**.
