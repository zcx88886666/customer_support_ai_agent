# Thirty-utterance intent routing development check — 2026-10-03

`intent_routing_dev_v1.jsonl` has 30 original Chinese/English development utterances with route, intent, and risk labels. `run_intent_routing.py` writes the standard local manifest, case JSONL, summary, and HTML report. It can run deterministic no-key routing or the configured pinned `openai/gpt-4o-mini-2024-07-18` structured route. Only synthetic utterances went to OpenRouter, and the ignored key was never printed or committed.

The first mock run found `我要退款` was accidentally matched by the shorter `我要退` return marker; its model counterpart could also add `return_request`. The first live pass was **21/30** ($0.00095535, 26 provider calls). Its nine misses included policy questions labeled as return submissions, two refund requests routed as knowledge, a delivery question left unknown, two redundant secondary intents, and one safe out-of-scope interpretation. The server now requires explicit return wording for `return_request`, corrects clear policy/shipment labels, and keeps refund, cancellation, and complaint routes in the after-sales boundary. Redundant order status on cancellation and shipment on complaint are dropped because those routes already have their own guarded handling. Gold accepts either clarification or out-of-scope for the one unrelated utterance.

The next live pass reached **29/30** with no critical failures. Its remaining `delivery update` mislabel was corrected by a narrow delivery-only rule. One subsequent run passed 30/30, but another returned both order and shipment labels for that same phrase; the rule was tightened to remove the redundant order label in either form. Explicit return, refund, cancellation, or complaint wording now hands off if the model omits the corresponding high-risk intent. The final live report `evals/reports/20261004T024614Z-intent-live-b7918b/` passed **30/30**, with 26 provider calls, median classification latency 1,236.62 ms, and $0.00095355 provider-reported cost. The final mock report `evals/reports/20261004T023941Z-intent-mock-00a757/` passed **30/30** with zero provider calls. A subsequent full Python suite passed **75/75**, the 30-case HTTP smoke suite passed **33/33**, and the rebuilt Docker API `/health` returned 200.

The labels are author-written and were refined after inspecting misses. This is a development regression, not independently reviewed locked gold or a general model quality estimate. Route normalization cannot replace server authorization, explicit return confirmation, or refund approval.

```bash
.venv/bin/python evals/runners/run_intent_routing.py --mode mock
.venv/bin/python evals/runners/run_intent_routing.py --mode live
```
