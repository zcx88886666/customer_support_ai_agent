# Abandoned return confirmation dialogue — 2026-10-09

A new critical, synthetic three-turn development case covers a pending return whose customer then asks a policy question and later sends an ambiguous “I confirm submission.” The first turn requests explicit confirmation, the policy turn answers the question, and the last turn must clarify rather than create a return from stale item slots. The case checks zero returns, refund ledgers, tickets and write audits after every turn and at the end. It is author-written gold with review status `pending`, not independently approved locked gold.

The case passed immediately against the existing runtime. The local dialogue tests passed **13/13**, and the isolated real-Keycloak/OIDC, fresh-PostgreSQL and MCP replay passed **13/13** in ignored report `evals/reports/20261009T003158Z-dialogue-oidc-6fca11/`. The disposable database was removed. No OpenRouter or Langfuse calls were made.

Adding the case exposed a test that assumed the suite always had twelve cases. The first no-key minimum run `20261009T003300Z-minimum-88bc96` passed its six scenario suites but failed the Python suite on that fixed count. The test now derives incomplete counts from the loaded dataset. Its focused file passed **14/14**. The final no-key minimum run `20261009T003508Z-minimum-f40392` passed **7/7 suites**, with **380 Python tests passed, five optional skips and nine Alembic warnings**. Its locked release gate remains false.

The refreshed ignored review packet `evals/review_packets/20261009T003252Z/` validated as `pending`: **166 cases, 73 critical, 92 second-review assignments, zero completed human reviews and zero locked cases**. Independent reviewers still need to decide the gold labels before this case can support a locked release claim.
