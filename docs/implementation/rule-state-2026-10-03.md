# Expanded refund rule-state campaign — 2026-10-03

The existing [Hypothesis refund action-sequence property](../../tests/test_refund_stateful.py) now accepts `STATEFUL_REFUND_EXAMPLES` for a bounded extended run, capped at 5,000. The normal development suite still runs 100 generated examples. Each example creates a fresh in-memory SQLite business world, attempts up to 18 actions from receipt, passing/failing inspection, proposal, approval/rejection, issue, and retry, then checks the ledger, approval, inspection, receipt, item refunded quantity and cents, and issuance audit after every action. Invalid action orders are expected to fail through the domain service without leaving a partial transaction.

The extended command completed in 85.39 seconds with **2,000 passing generated action sequences, zero failing, and 202 invalid generated attempts** as reported by Hypothesis. Three explicit example sequences also ran in the same test definition; the generated count is the Hypothesis statistic. The full default Python suite then passed **70/70** in 12.36 seconds. This is rule-engine property coverage and is not an LLM Agent success rate. The earlier 1,000 generated paid-allocation checks remain separate.

```bash
STATEFUL_REFUND_EXAMPLES=2000 .venv/bin/pytest -q tests/test_refund_stateful.py::test_random_refund_action_sequences_preserve_money_and_approval --hypothesis-show-statistics
.venv/bin/pytest -q
```

This campaign uses SQLite and one synthetic order/item family. PostgreSQL concurrent worker overlap, rollback, and process restart have separate integration reports. Broader rule-state families for ownership, multi-item allocation, and cross-role concurrent writes remain open.
