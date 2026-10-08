# Adversarial validation of generated data (2026-10-08)

The synthetic CSV validator now checks unique customer, profile, product, and after-sales keys against reported counts. It also checks that a shipment's status and delivery timestamp agree with its delivery event, reports missing or trailing rows instead of silently accepting them, and writes validation results back to the quality report.

Eight focused tests changed CSV data while updating the reported SHA-256 (and counts where relevant). They cover contradictory delivery states or times, duplicate returns and root entities, missing or trailing shipment rows, and a missing product referenced by a return. All eight passed after the validator changes.

Three further fixtures changed grouped CSVs while keeping their hashes and relationship totals consistent. A duplicate item with a matching duplicate allocation and adjusted payment previously passed; a duplicate shipment-event ID and duplicate conversation-message ID also passed. The validator now checks IDs within each small order, shipment, or ticket group. All three tests failed before this change and passed afterward. This keeps the million-order path bounded without a global set of every item and event ID.

A fixed-seed 100,000-order world was generated and independently validated in a temporary directory: `orders_checked=100000`, `violations=0`, and `file_hashes_match=true` (4.14 seconds for the combined check). The final no-key minimum run `20261008T071127Z-minimum-e707d2` passed 7/7 suites, including 366 Python tests with five optional skips. These are synthetic development checks; distribution calibration and independent fixture review remain open.

After the grouped-ID change, a fresh fixed-seed **1,000,000-order** world generated and validated in a temporary directory in **43.0 seconds**, with zero violations and matching hashes. The temporary output was removed. The final no-key minimum run `20261008T071636Z-minimum-921418` passed **7/7** suites, with **369 Python tests passed and five optional skips**.
