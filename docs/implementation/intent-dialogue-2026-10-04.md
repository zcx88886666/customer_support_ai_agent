# Intent dialogue development evaluation

On 2026-10-04, a new 12-case, isolated SQLite HTTP suite exercised multi-turn Agent behavior that the 30-utterance classifier suite cannot measure. Cases cover return slots, confirmation, tracking order choice, owned and foreign package selection, order changes, repeated unknown and missing-slot limits, explicit human handoff, a refund inquiry, and a read-only policy/shipment composite. The scorer compares each turn's HTTP status, Agent status/route/revision and relevant answer or options, then checks return, ledger, ticket, ownership, and audit state. Fixture data are synthetic. All labels are author-written `dev` with `review.status: pending`.

Ignored report `evals/reports/20261004T115351Z-intent-dialogue-d041e0/` passed **12/12**, with zero incomplete and zero critical failures. The existing mock route-label run `evals/reports/20261004T115429Z-intent-mock-b1fbb5/` passed **30/30**. The full Python suite passed **126/126**; `git diff --check` passed. Both reports are development evidence, not a human-reviewed locked result. The dialogue suite has not yet been replayed as a whole with real OIDC/PostgreSQL or a live model.

```text
.venv/bin/python evals/runners/run_intent_dialogue.py         12/12 pass
.venv/bin/python evals/runners/run_intent_routing.py --mode mock  30/30 pass
.venv/bin/pytest -q                                           126 passed
```
