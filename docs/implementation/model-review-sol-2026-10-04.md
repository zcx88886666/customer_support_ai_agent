# GPT-6 Sol targeted advisory review, 2026-10-04

The [full GPT-6 Luna pass](model-review-luna-2026-10-04.md) identified **62 non-accept cases**: 35 `revise` and 27 `needs_context`. Those case IDs alone were sent to `openai/gpt-6-sol` through the same committed [`review-v1` Prompt](../../prompts/releases/review-v1.json), [criteria v3](eval-review-criteria-v3.md), packet source hashes, reference facts, strict schema and evidence audit. [OpenRouter lists Sol at $2/M input and $10/M output tokens](https://openrouter.ai/openai/gpt-6-sol), substantially below the earlier Astra Pro list price. The targeted pass used a separate $1 cumulative packet stop and wrote to ignored `evals/review_packets/20261004T211845Z/model_review_gpt6_sol/`.

The pass saved **62/62 selected cases**; `pending: 100` in its manifest means the 100 Luna-accepted cases were deliberately not selected. Sol returned **20 accept, 29 revise, 13 needs context, 0 reject**. Provider-reported usage was **159,971 input tokens, 29,886 output tokens, $0.67427310**. There were no failed calls. Six exact-string evidence-quote checks flagged mismatches; inspect those per-case JSON records before adopting a finding.

| Luna decision → Sol decision | Cases |
|---|---:|
| revise → revise | 25 |
| revise → accept | 10 |
| needs context → needs context | 13 |
| needs context → accept | 10 |
| needs context → revise | 4 |

The models disagree on **24/62** targeted decisions. Sol accepted `smoke-14` because the return request lacks explicit submission confirmation; the [agent](../../apps/api/resolveai/agent.py) and [domain service](../../apps/api/resolveai/domain.py) support that reading. Sol also accepted `core-chat-stale-proposal`; the [business scorer](../../evals/runners/run_business.py) sorts proposal statuses, so Luna's proposed reversal was incorrect. Sol and Luna both propose strengthening `smoke-03`'s undelivered-window explanation and `smoke-07`'s refund-prerequisite assertions. Sol's suggested risk-tier reduction for `smoke-03` is still an interpretation to adjudicate, since the criteria also list unsafe composite evidence as critical.

Sol marked `core-proposal-replay-pending` for a possible missing `worker_issued` observation. This is a candidate for checking against the scripted workflow and scorer, not a verified correction. Other disagreements and all `needs_context` cases are in the two ignored per-case directories. No model judgment changes gold, completes the human review, or makes a locked release split. The v6 requirement for two human reviewers on all critical cases, at least 20% of normal cases double reviewed, adjudication, and leakage-safe grouping remains open.
