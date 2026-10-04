# GPT-6 Luna advisory evaluation-case review, 2026-10-04

The independent model reviewer uses the committed [`review-v1` Prompt release](../../prompts/releases/review-v1.json), [criteria v3](eval-review-criteria-v3.md), source-hash-locked [162-case packet](minimum-review-packet-2026-10-04.md), checked-in demo seed facts, a strict response schema, and JSON-pointer evidence checks. Its default model is now `openai/gpt-6-luna`; each model has a separate ignored output directory. [OpenRouter lists Luna at $0.10/M input and $0.50/M output tokens](https://openrouter.ai/openai/gpt-6-luna). The runner stops before the next request when cumulative packet cost reaches $1; this cannot cap an in-flight call.

The saved ignored directory is `evals/review_packets/20261004T211845Z/model_review_gpt6_luna/`. The live run reviewed **162/162** cases: **100 accept, 35 revise, 27 needs context, 0 reject**. OpenRouter reported **410,301 input tokens, 96,464 output tokens, and $0.09740739** for saved results. Three invalid structured outputs cost another **$0.00239590**, for **$0.09980329 total** recorded packet cost. The runner rejected those invalid results, counted their cost, and succeeded on a later request. `manifest.json`, per-case JSON, `model_review.csv`, `failed_calls.jsonl`, and `errors.log` preserve the evidence. The quote audit flags **15 exact-string mismatches across 12 cases**; seven are equivalent JSON with different ordering or whitespace, and the others include key-value fragments quoted against scalar pointers. These flags need inspection; they do not prove the cited fact is wrong.

| Suite | Accept | Revise | Needs context |
|---|---:|---:|---:|
| Smoke | 17 | 10 | 3 |
| Core business | 18 | 2 | 10 |
| Intent route | 14 | 14 | 2 |
| Intent dialogue | 1 | 0 | 11 |
| Policy positive | 19 | 0 | 1 |
| Policy near-negative | 20 | 0 | 0 |
| Collaboration | 11 | 9 | 0 |

## Quality audit and limits

The model identified a plausible missing assertion in `smoke-03`: the gold requires only an undelivered-status phrase, while criteria v3 also require explaining that the delivered-goods return window has not started. `smoke-07` similarly requires only supervisor approval wording and may need receipt and inspection prerequisites. These are **proposals for human adjudication**, not gold changes.

Two checked model findings are incorrect. In `smoke-14`, Luna treated the customer's request plus item details as authorization to create a return. The case has no explicit `confirmed` value, while the [agent](../../apps/api/resolveai/agent.py) and [domain service](../../apps/api/resolveai/domain.py) require confirmation before a return write. In `core-chat-stale-proposal`, Luna proposed reversing `proposal_statuses`; the [business scorer](../../evals/runners/run_business.py) sorts actual proposal statuses before comparison, so the existing `['issued', 'stale']` gold order is correct. These errors show that a valid schema and evidence pointer do not establish correct reasoning.

Several `needs_context` decisions concern exact HTTP status or intent taxonomy claims not fully spelled out in the submitted reference material. The corresponding API or scorer code can be inspected during human adjudication; the model's uncertainty must not be converted to an accept without that check. No model decision changes case gold or constitutes either of the human reviews required by v6. The human `reviewer_a.csv` and second-review assignments remain pending, and the packet cannot be promoted to a leakage-safe locked split because each suite still has one connected fixture/template group. The release gate remains false.

To refresh human/model disagreements after the human sheet is filled, run:

```bash
.venv/bin/python scripts/run_model_review.py evals/review_packets/20261004T211845Z --refresh-only
```
