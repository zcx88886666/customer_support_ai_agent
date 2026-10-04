# Published policy retrieval development gate — 2026-10-03

`run_policy_runtime.py` scores the published four-clause demo bundle against twenty relevant questions from `policy_retrieval_v1.jsonl` and twenty near-domain negatives from `policy_retrieval_negatives_v1.jsonl`. It can create either an in-memory SQLite world or a fresh migrated isolated Docker PostgreSQL database with the same version-scoped runtime index. Each run writes manifest, case JSONL, summary, and HTML locally. These questions and labels are author-written development material, not independently reviewed policy gold.

The initial SQLite run found the expected clause in **12/20 at Hit@5**, while **18/20** negatives returned no clause. English questions had no Chinese clause overlap, several Chinese paraphrases missed, and generic product questions retrieved unrelated clauses. A conservative after-sales scope check now abstains on questions without a return, refund, delivery, warehouse, or seven-day topic. A small versioned Chinese/English synonym map bridges clear terms such as `delivery→签收`, `refund→模拟退款`, and `数字商品→非实物`; it does not make a model decide eligibility. Search index version changed from `local-gram-v1` to `local-gram-v2`, so existing PostgreSQL indexes must be refreshed before use.

The final SQLite report `evals/reports/20261004T090335Z-policy-runtime-sqlite-1e40b6/` scored **20/20 Hit@5**, **19/20 Hit@1**, and **20/20 negative abstentions**. The final fresh PostgreSQL report `evals/reports/20261004T090338Z-policy-runtime-postgres-383fed/` scored **20/20 Hit@5**, **18/20 Hit@1**, and **20/20 negative abstentions**, with four indexed clauses and zero provider calls. Their manifests include dataset, policy content, index version, and local Prompt release hashes. An early post-change HTTP smoke run failed one composite question because its wording said “七天政策” without another scope marker. Adding `七天/七日` to the scope gate restored **33/33** smoke executions. The full Python suite passed **77/77**.

The Docker API was rebuilt; `scripts/index_policies.py` refreshed four active clauses. `scripts/verify_policy_search.py` passed bundle scoping, stale-index rejection, live draft indexing, and rollback on current Alembic head `7c461acdb21e`. `/health` returned 200. The verifier now reads Alembic's current head instead of comparing a stale hardcoded revision.

These results cover the published four-clause demo only. The earlier 40-clause/81-query offline embedding benchmark remains a separate negative result; its human review and safe semantic abstention gate are still open. The domain service, rather than retrieved text, decides return eligibility and refund authority.

```bash
.venv/bin/python evals/runners/run_policy_runtime.py --dialect sqlite
POLICY_RUNTIME_PG_ADMIN_URL=postgresql://resolveai@<isolated-postgres-host>:5432/postgres \
  .venv/bin/python evals/runners/run_policy_runtime.py --dialect postgres
docker compose --env-file .env -f infra/compose/compose.yaml exec -T api python scripts/index_policies.py
docker compose --env-file .env -f infra/compose/compose.yaml exec -T api python scripts/verify_policy_search.py
```
