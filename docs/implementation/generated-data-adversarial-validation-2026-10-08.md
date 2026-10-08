# Adversarial validation of generated data (2026-10-08)

The synthetic CSV validator now checks unique customer, profile, product, and after-sales keys against reported counts. It also checks that a shipment's status and delivery timestamp agree with its delivery event, reports missing or trailing rows instead of silently accepting them, and writes validation results back to the quality report.

Eight focused tests changed CSV data while updating the reported SHA-256 (and counts where relevant). They cover contradictory delivery states or times, duplicate returns and root entities, missing or trailing shipment rows, and a missing product referenced by a return. All eight passed after the validator changes.

A fixed-seed 100,000-order world was generated and independently validated in a temporary directory: `orders_checked=100000`, `violations=0`, and `file_hashes_match=true` (4.14 seconds for the combined check). The final no-key minimum run `20261008T071127Z-minimum-e707d2` passed 7/7 suites, including 366 Python tests with five optional skips. These are synthetic development checks; distribution calibration and independent fixture review remain open.
