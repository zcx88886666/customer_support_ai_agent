# Approval-before-proposal evaluation check (2026-10-08)

The database-first refund scorer already rejected a supervisor approval recorded after refund issuance. It did not check whether the same approval was recorded before the proposal existed. A new isolated `approved_refund` mutant backdated the persisted approval by one hour relative to its proposal. The workflow still reached a ledger and the old scorer let the mutant survive.

The scorer now requires a proposal creation timestamp and checks `proposal.created_at <= approval.decided_at <= ledger.issued_at` with normalized timestamps. The new mutant fails `refund_authorized`; the targeted application campaign passed **18 killed, zero survived, zero invalid** in report `20261008T074432Z-business-mutations-c2bb7e`. Focused business-mutant and scorer tests passed **17/17**. The final no-key minimum run `20261008T074453Z-minimum-8cf61a` passed **7/7** suites with **369 Python tests passed and five optional skips**.

This changes evaluation only. Runtime supervisor authorization, proposal rules, and refund issuance still use domain and role checks. The development gold remains author-written and the locked human-reviewed release gate remains false.
