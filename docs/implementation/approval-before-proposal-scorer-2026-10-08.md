# Refund causal-chronology evaluation checks (2026-10-08)

The database-first refund scorer already rejected a supervisor approval recorded after refund issuance. It did not check whether the same approval was recorded before the proposal existed. A new isolated `approved_refund` mutant backdated the persisted approval by one hour relative to its proposal. The workflow still reached a ledger and the old scorer let the mutant survive.

The scorer first required `proposal.created_at <= approval.decided_at <= ledger.issued_at` with normalized timestamps. The new approval mutant failed `refund_authorized`; that intermediate targeted campaign passed **18 killed, zero survived, zero invalid** in report `20261008T074432Z-business-mutations-c2bb7e`.

Three further mutants backdated a receipt before its return, an inspection before its receipt, and a proposal before its inspection. All three survived the intermediate scorer. The final scorer requires the complete persisted sequence `return.created_at <= receipt.received_at <= inspection.inspected_at <= proposal.created_at <= approval.decided_at <= ledger.issued_at`. Each new mutant now fails `refund_authorized`; the final campaign `20261008T074801Z-business-mutations-c87cca` passed **21 killed, zero survived, zero invalid**. Focused business-mutant and scorer tests passed **17/17**. The final no-key minimum run `20261008T074824Z-minimum-4dea6d` passed **7/7** suites with **369 Python tests passed and five optional skips**.

This changes evaluation only. Runtime supervisor authorization, proposal rules, and refund issuance still use domain and role checks. The development gold remains author-written and the locked human-reviewed release gate remains false.
