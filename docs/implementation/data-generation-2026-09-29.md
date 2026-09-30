# Synthetic data generation run — 2026-09-29

The generator and validator were run on WSL2 Linux x86_64 with 20 reported CPUs and 15 GiB RAM. These are measured generation and CSV validation results, not PostgreSQL import or API load results. All records are synthetic; the distributions are project assumptions without external calibration.

| Profile | Orders | Items | Shipment events | Completed simulated returns/refunds | Tickets | Conversations | Generated size | Generate | Validate |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| realistic | 100,000 | 125,000 | 596,551 | 2,310 | 6,667 | 13,334 messages | 90 MiB | 2.733 s | 100,000 orders; 0 violations; hashes match |
| scale | 1,000,000 | 1,250,000 | 5,965,517 | 23,090 | 66,667 | 133,334 messages | 895 MiB | 27.164 s | 1,000,000 orders; 0 violations; hashes match |

Commands:

```bash
.venv/bin/python data/generator/generate.py --profile realistic --output data/generated/realistic-100k-v3
.venv/bin/python data/generator/validate.py data/generated/realistic-100k-v3
.venv/bin/python data/generator/generate.py --profile scale --output data/generated/scale-1m-v3
.venv/bin/python data/generator/validate.py data/generated/scale-1m-v3
```

Both runs used seed `20260929` and fixed clock `2026-09-29T12:00:00+00:00`. The large CSV files and full per-file SHA-256 manifests remain under ignored `data/generated/` directories. Re-run the commands to recreate them. The independent validator checks customer and product references, item allocations against payments, event order, receipt → inspection → proposal → approval → ledger relationships, refund amounts, and conversation ownership. The generated data has not been imported into PostgreSQL or load tested in this environment.
