# Separate review stories and a one-human validation path

> Verified on 2026-10-11 UTC. All cases use synthetic data and remain author-written development cases. One human review is available; the original v6 two-human release gate remains false.

## Change

The isolated `independent_review_v1` world adds two customers and four orders without changing the 25-order demo seed. Harbor has a two-unit order delivered eight days ago with a ten-day favorable promise; Ridge has an in-transit order and a delivered special-notice product. Each world has its own customer, orders, source story and template family, separate from the earlier demo fixtures.

Seven versioned `review_pilot_*_v1.jsonl` sources contain **14** cases: two each for smoke, terminal core, intent routing, multi-turn dialogue, policy positive, policy near-negative, and paired collaboration. The core cases exercise a one-unit return through warehouse inspection, supervisor approval and one simulated refund, and a special-notice request that creates one review ticket without a return or refund. The `run_review_pilot.py` runner replays the cases against isolated SQLite databases, measures provider calls, clears OpenRouter/Langfuse credentials, and writes local manifest/JSONL/summary/HTML artifacts.

The packet generator includes the new dataset hashes, independent fixture code hash, and underlying demo seed code hash. The optional advisory model reviewer now receives source-derived facts for those orders and refuses changed source or packet content before making a call. `validate_single_human_review.py` reuses the full packet integrity and form checks, then reports `single_review_complete` when reviewer A alone finishes valid decisions under one reviewer identity. It rejects known model identities and never reports a locked release or treats model output as a second human review. The original two-human validator and grouped candidate gate remain in place.

## Evidence

| Check | Observed result |
|---|---|
| New isolated pilot | Run `20261011T025441Z-review-pilot-c2b103`: **14/14** passed, zero critical failures, **zero measured provider calls**, Langfuse disabled, `release_gate_pass: false`. |
| Adjacent packet, fixture, one-human and advisory-model tests | **33/33 passed** after the provenance fixes. |
| Full no-key minimum | Run `20261011T025650Z-minimum-c178b4`: **7/7** suites; Python **438 passed, 5 skipped**. The locked release gate is false. |
| Refreshed packet | **180 cases, 84 critical, 104 second-review assignments, zero human reviews, zero locked**. Every suite has three connected components; all-suite graph has four. Source and packet validation returned `pending` with no structural errors. |

An initial clean-shell minimum run (`20261011T025511Z-minimum-58a411`) passed Python tests and policy retrieval but failed five child suites with `ModuleNotFoundError: No module named 'evals'`. The launcher now sets its own child import path. The fresh `20261011T025650Z-minimum-c178b4` run passed all seven suites. The local TestClient replay stalled inside the restricted sandbox; the same no-key pilot completed in about 2.4 seconds outside that restriction.

The new two world groups total 14 cases. Their proportion is too small to make a 30–50% locked assignment with coverage in every suite, and one human review cannot satisfy the separate two-human requirement. A single reviewer can check the final development packet without creating a misleading release label. See the [handoff](../HUMAN_REVIEW_HANDOFF.md).
