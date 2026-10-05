# Local PostgreSQL backup and restore drill, 2026-10-05

[`verify_postgres_backup_restore.py`](../../scripts/verify_postgres_backup_restore.py) creates two separate, labeled Docker PostgreSQL 17 instances from the cached image. Each case first builds a migrated synthetic business database through the real return/refund paths, recovers from an uncommitted client kill, and writes a LangGraph PostgresSaver checkpoint. It then writes a private `pg_dump` archive, restores it with `pg_restore --single-transaction` into the other instance, and checks the restored database before replaying any action. Both instances and volumes are validated before use or cleanup.

The verifier compares all public table names, counts and canonical full-row hashes, plus column type modifiers, constraints, indexes, extensions, sequence definitions, last values and `is_called` state. JSON row hashing preserves exact numeric tokens. A fresh checkpointer reads the restored stage. Two fresh-process business retries must leave every restored table unchanged; the return reuses its original idempotency key and the approved refund worker issues nothing further. Archives and raw snapshots stay under ignored `evals/reports/<run_id>/`, with the archive mode set to `0600` and its SHA-256 recorded. The manifest records the source commit and verifier hashes, actual image/container IDs, and resource cleanup.

## Actual evidence

- Final run `20261005T055618Z-backup-restore-7fed2e`: **2/2 cases, 65/65 checks**, zero incomplete cases. Both owned instances and volumes were removed after the checks. The return and refund cases each restored **29 public tables**; archives were **58,385** and **59,265 bytes**. The measured local restore operations took **0.1590** and **0.1694 seconds**. Those timings cover `pg_restore` only and are not recovery-time objectives.
- The initial successful run `20261005T011204Z-backup-restore-50152f` passed **2/2, 63/63**. A follow-up regression found that equal sequence last values can still produce different next IDs when `is_called` differs. It failed before the comparator included that flag, then passed. The final real run verified the additional two sequence-state checks.
- A fresh read-only review found that `varchar` lengths were missing from the column metadata and high precision JSON numbers were rounded during hashing. The real isolated PostgreSQL mutation `varchar(3) → varchar(4)` failed before the fix and passed after switching to complete `format_type` metadata. A high precision numeric collision regression also went red→green with lossless `Decimal` serialization. **13/13 focused tests** passed with the optional PostgreSQL schema database enabled.
- Both source fixtures passed the existing client-kill rollback and fresh-process idempotency checks before backup. Final full suite: `PROMPT_RELEASE=specialists-dev-v1 .venv/bin/python -m pytest -q` → **209 passed, 2 optional PostgreSQL tests skipped** in 21.40 seconds. The new schema test passed separately against an isolated real PostgreSQL database, which it dropped after use; the other deadline test passed in its earlier slice. No Critical/Important review findings remain after this single red→green fix pass.

```bash
.venv/bin/python scripts/verify_postgres_backup_restore.py
```

Docker and the cached `pgvector/pgvector:pg17` image are required. The script uses synthetic data, a loopback PostgreSQL port, a new source and target container/volume, and no model or Cloud calls. Failed resources and safe diagnostics remain in the ignored report area for review.

This establishes a **local logical backup and independent-server restore** with intact storage and local archives. A copy on a separate offline medium, protection/retention of historical backups and exports, and recovery from damaged media require separate decisions and evidence. The human-reviewed locked release gate remains false.
