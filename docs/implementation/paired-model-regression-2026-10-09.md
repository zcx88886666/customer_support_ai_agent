# Current-code paired model regression — 2026-10-09

The existing 11-case synthetic development paired runner was replayed against current code with `openai/gpt-4o-mini-2024-07-18`, the pinned model in the local registry. OpenRouter still [lists this model and its structured-output support](https://openrouter.ai/openai/gpt-4o-mini-2024-07-18). The runner used the stored local key and no Langfuse credentials.

Ignored report `evals/reports/20261009T004209Z-paired-model-144f95/` records **11/11 single** and **11/11 collaborative** passes, with **11 ties and zero wins** for either mode. Each mode made 20 provider calls. The provider reported 4,290 input tokens in each mode, 567/547 output tokens, and costs of **$0.0009837/$0.0009717** respectively (**$0.0019554 combined**); no call lacked a cost field. Median observed latency was 3,524.86ms single and 3,404.44ms collaborative. These small development measurements do not establish a collaboration advantage.

The run was `PYTHONPATH=apps/api:. .venv/bin/python evals/runners/run_paired_model.py --scope development` outside the restricted sandbox because the FastAPI TestClient portal does not complete reliably inside it. It used isolated SQLite per execution. The separate 13-case multi-turn dialogue set was not run against a live model in this pass, and all independent human review and locked comparison requirements remain open.
