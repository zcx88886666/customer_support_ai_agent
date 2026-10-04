# Cost-efficient advisory model review, 2026-10-04

The review runner now defaults to the exact `deepseek/deepseek-v3.2` model, while preserving the previous Astra Pro results in a separate directory. It uses the same hash-locked [review prompt](../../prompts/catalog/eval_case_review.yaml), [release manifest](../../prompts/releases/review-v1.json), [criteria v3](eval-review-criteria-v3.md), strict JSON schema, synthetic seed facts, evidence audit and 162-case [review packet](minimum-review-packet-2026-10-04.md). DeepSeek results belong to the ignored `evals/review_packets/20261004T211845Z/model_review_deepseek_v32/`; the earlier Astra results remain in `model_review/`. No model result is a human review or an automatic gold change.

[OpenRouter's DeepSeek V3.2 page](https://openrouter.ai/deepseek/deepseek-v3.2) describes a reasoning model with JSON-schema output support and lists **$0.2088/M input tokens** and **$0.3096/M output tokens** at the time checked. This is about 48× lower input and 161× lower output list price than [Astra Pro's $10/$50](https://openrouter.ai/openai/gpt-6-astra-pro). Repricing the previous 17 cases' *Astra token counts* gives **$0.034527** versus their $1.610210 actual Astra cost; linear extrapolation to 162 would be **$0.329**, but DeepSeek may tokenize or generate differently and the remaining cases have different lengths. The new run stops before its next call when **$1** of provider-reported or fallback-estimated packet spend has accumulated. This is a spending guard, not a prediction or a guaranteed hard ceiling for an in-flight call.

## Live status

The four-case calibration was attempted on `smoke-03`, `smoke-07`, `core-chat-approved-refund` and `policy-window-01`. The first request returned **HTTP 403** before any review was saved. A tiny synthetic probe returned the provider error category **“Key limit exceeded (total limit)”**. A read-only [current-key check](https://openrouter.ai/docs/api/api-reference/api-keys/get-current-key) reported **$6 limit, $6.0241278 usage and $0 remaining**. The failure is therefore at the key spending limit; no DeepSeek quality, latency, or actual per-case cost has been measured. The ignored `errors.log`, `blocker.json` and manifest preserve this state: **0 reviewed, 162 pending**.

The default model's request body and strict schema were verified with an offline mock transport, and the review/packet tests passed **7/7**. The older Astra result remains **17/162** and must not be counted as DeepSeek validation.

## Resume after key limit change

Raise this OpenRouter key's total spending limit to at least **$8** and ensure at least **$1** in usable account credit; the inference key cannot raise its own limit. Then run a four-case calibration:

```bash
.venv/bin/python scripts/run_model_review.py evals/review_packets/20261004T211845Z --case-id smoke-03 --case-id smoke-07 --case-id core-chat-approved-refund --case-id policy-window-01 --limit 4
```

Inspect the decisions, evidence pointers, quote audit and provider-reported cost in `model_review_deepseek_v32/`. If the calibration is sound, run the same command without `--case-id` and `--limit` to review the rest under the $1 packet stop. `--refresh-only` updates the human/model disagreement file without an API call. The [human reviewer sheet](../../evals/review_packets/20261004T211845Z/reviewer_a.csv) remains independent and blank.
