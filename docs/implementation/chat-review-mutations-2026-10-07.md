# Review-ticket mutation checks — 2026-10-07

The isolated [business mutation runner](../../evals/runners/run_business_mutations.py) now runs eleven faults across the direct business fixture and the core chat expired-window fixture. Two new faults exercise the review-ticket scorer:

| Fault | Deliberate database error | Failed check |
|---|---|---|
| `review_audit_wrong_actor` | Create the ticket but record its review audit under another customer | `chat_review_ticket` |
| `review_ticket_wrong_order` | Return the ticket ID while linking the stored ticket to another owned order | `chat_review_ticket` |

The test first failed with the previous nine-mutant count. After the runner change, `PYTHONPATH=apps/api:. .venv/bin/pytest -q tests/test_business_mutations.py::test_targeted_business_mutations_are_detected` passed **1/1**. Ignored report `evals/reports/20261007T085840Z-business-mutations-0f5db5` records **11 killed, zero survived, zero invalid**; each new fault failed specifically at `chat_review_ticket` while other terminal checks stayed green. The runner records both dataset hashes and uses a fresh SQLite database per mutation. The final no-key run `evals/reports/20261007T085923Z-minimum-ba8a7b` passed **7/7** suites, with **296 Python tests passed and five optional skips**. No provider or Langfuse call was made.

These are deliberate application faults against author-written development cases. They do not establish human-reviewed gold or the locked release gate.
