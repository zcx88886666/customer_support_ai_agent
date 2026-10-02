# PostgreSQL policy search verification — 2026-10-02

The Docker API applied policy-index revision `0e7b4cb91a62` on PostgreSQL 17.11 with pgvector 0.8.6. A later data repair made Alembic head `8a512e96af34`; see [rollback verification](policy-rollback-2026-10-02.md). Startup indexed all four clauses in active `policy-demo-v1`. The new table stores a version and bundle hash with each clause, a `tsvector` search field, and a 128-dimensional local character-gram vector. GIN and HNSW indexes exist. PostgreSQL retrieval combines full-text and vector candidate ranks with reciprocal-rank fusion. SQLite keeps the deterministic local path.

Verification commands and actual results:

```text
.venv/bin/pytest -q
41 passed in 4.82s (latest run outside the restricted sandbox)

docker compose --env-file .env -f infra/compose/compose.yaml exec -T api python scripts/verify_policy_search.py
revision 8a512e96af34; four demo clauses; GIN and HNSW indexes present;
live draft index passed; bundle scope passed; stale index rejected; rollback passed
```

The script creates a temporary draft policy, verifies that publication builds its search rows, checks bundle isolation, changes its index version to `stale`, and confirms that PostgreSQL retrieval returns no evidence and activation rejects the stale index. After refreshing it, the script publishes the draft and rolls back to the original policy. It rolls back the entire test transaction. `七天无理由退货` retrieved `clause-window` first; `火星天气` returned no match.

The vector is a deterministic local character sketch. It gives a cheap, reproducible retrieval baseline; it is not a semantic embedding model. The planned embedding choice and quality benchmark, larger corpus recall study, and human review are still open. PostgreSQL retrieval fails closed if its bundle index is missing or stale.
