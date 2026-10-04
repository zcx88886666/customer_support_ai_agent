# Split-return full-workflow property — 2026-10-03

The refund rule-state tests now cover every quantity partition for a three-unit synthetic order item: `[3]`, `[1, 2]`, `[2, 1]`, and `[1, 1, 1]`, each with and without idempotent request/ledger replay. Hypothesis exhausted **8/8 distinct combinations**, with no failing or invalid examples. Each partition creates committed customer returns, warehouse receipts and passing inspections, supervisor-approved proposals, and refund ledgers in fresh SQLite business worlds.

Before each valid return, a different customer is denied; after creation, that customer cannot hijack its idempotency key. An attempt to overcommit three more units fails. After each issued part, the item refunded cents equal `floor(paid_cents × cumulative_quantity / purchased_quantity)`, equal the ledger sum, and have exactly one issuance audit per ledger. The final three units account for all original paid cents, including the rounding remainder. The full Python suite passed **72/72**.

This is a complete domain workflow over one synthetic item family, not a concurrency claim. PostgreSQL overlap and process-kill checks are recorded separately. Broader multi-item, cancellation, and external-data rule campaigns remain open.

```bash
.venv/bin/pytest -q tests/test_refund_stateful.py::test_partitioned_returns_preserve_paid_allocation_and_ownership --hypothesis-show-statistics
.venv/bin/pytest -q
```
