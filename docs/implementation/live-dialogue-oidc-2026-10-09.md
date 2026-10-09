# Live dialogue over real OIDC, PostgreSQL and MCP — 2026-10-09

The isolated dialogue verifier now has an opt-in `--live-model` mode. It starts a freshly migrated PostgreSQL database and private API/MCP pair per synthetic case, signs in with real Keycloak tokens, restarts the API after the first turn of a multi-turn case, and checks that another customer cannot resume the thread. The child API receives the stored OpenRouter key only in live mode; Langfuse and OTel exporters remain disabled. Each turn records reported model attempts, tokens, cost and unknown-usage count without storing the raw answer or token in the public case result.

The live run `evals/reports/20261009T005700Z-dialogue-oidc-live-eb64ac/` passed **13/13** cases with **38 model attempts, $0.00152415 provider-reported cost and zero unknown-usage calls**. The aggregate stop was $0.02 of reported cost; it is checked between cases, so an in-progress case can cross it. The report found zero critical failures. The owned disposable PostgreSQL container and volume were removed.

After the verifier change, the default no-key run `evals/reports/20261009T010037Z-dialogue-oidc-439f25/` also passed **13/13** and removed its database resources. The full no-key minimum `20261009T005904Z-minimum-6c0c0c` passed **7/7 suites**, with **387 Python tests passed, five optional skips and nine Alembic warnings**. The two new verifier tests check that only the model key reaches the private live API and that an early cost stop leaves remaining cases incomplete.

These are author-written development labels. No independent human gold review or locked release comparison has been completed, and `release_gate_pass` remains false in both reports.
