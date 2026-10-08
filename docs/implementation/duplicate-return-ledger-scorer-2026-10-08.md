# Duplicate return ledger scoring (2026-10-08)

The database-first scorer could accept two separately approved and audited ledgers tied to one return request if a forged second proposal, ledger and balance used a valid cumulative amount. A synthetic adversarial fixture for a three-unit item created a real first refund, then inserted a second proposal, approval, 351-cent ledger and matching audit for the **same return**. All old checks passed: counts, ownership, chronology, per-ledger amount and item balance.

The item reconciliation now rejects a return ID seen in more than one ledger for that item. The red regression passed after the change; the positive two-ledger case for two **different** returns still passes. Focused scorer/campaign tests passed **20/20**. The final no-key minimum `evals/reports/20261008T160217Z-minimum-2d208b` passed **7/7 suites**, including **375 Python tests passed and five optional skips**. `git diff --check` passed.

This is a synthetic scorer safeguard. The runtime's proposal and refund methods were not changed. The 25-mutant generated campaign still measures its earlier matrix and this separate adversarial fixture adds duplicate-return coverage. Independent human gold and the locked release gate remain open.
