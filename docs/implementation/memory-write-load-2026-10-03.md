# Bounded PostgresStore preference write-load — 2026-10-03

The [runner](../../evals/runners/run_memory_write_load.py) created a fresh migrated Docker PostgreSQL 17 database, initialized PostgresStore, and used 16 synthetic consenting customers across three concurrency stages. Each worker alternated 20 explicitly confirmed `language` corrections through the application memory adapter, committed each correction, then read the authoritative SQL/PostgresStore view in a new session. After all stages, consent withdrawal revoked all 16 SQL memory rows, and no corresponding PostgresStore namespace returned a value. There were no model/provider calls. The database `ra_memory_load_3ee8706e9b0b` and ignored report `evals/reports/20261004T012556Z-memory-load-8ee462/` remain locally for inspection.

| Concurrent workers | Confirmed writes / verified reads | Elapsed | Writes/s | Median / P95 write latency | Failed checks |
|---:|---:|---:|---:|---:|---:|
| 1 | 20 / 20 | 0.079 s | 252.03 | 1.94 / 15.43 ms | 0 |
| 5 | 100 / 100 | 0.368 s | 271.86 | 10.48 / 12.91 ms | 0 |
| 10 | 200 / 200 | 0.793 s | 252.15 | 22.27 / 26.18 ms | 0 |

The 20-operation stages are short bounded measurements, not sustained throughput or a service-level objective. Each operation includes an application write and a separate guarded read; the table's latency column times only the committed write. Startup, migration, customer creation, and final revocation are outside stage elapsed times. The one-worker P95 includes its first cold connection. The three stages used different customers and did not measure same-customer write contention. No real customer data or live demo database was used.

Reproduce against a separate PostgreSQL server with a local admin connection to `/postgres`:

```bash
MEMORY_LOAD_PG_ADMIN_URL=postgresql://resolveai@<isolated-postgres-host>:5432/postgres .venv/bin/python evals/runners/run_memory_write_load.py
```

The runner creates a uniquely named `ra_memory_load_*` database and leaves it for inspection. It writes only the ignored report summary, which contains no credentials. Longer duration, same-customer contention, HTTP/worker mixed traffic, and physical erasure of revoked SQL rows remain open.
