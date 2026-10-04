# Minimum evaluation review packet

On 2026-10-04, `scripts/prepare_minimum_review.py` generated an ignored local packet at `evals/review_packets/20261004T115723Z/` from the versioned synthetic development datasets. Its manifest locks source dataset hashes. The packet has **162 case records**: 30 smoke, 30 core business, 30 route labels, 12 intent dialogues, 20 policy positives, 20 policy near-negatives, and 20 paired collaboration cases. There are **69 critical** and **93 normal** cases. Reviewer A has 162 assignments; reviewer B has 88, covering all 69 critical cases and 19 normal cases (at least 20% of normal). It includes separate CSV sheets and an adjudication sheet. The script does not label or publish any case as locked.

The packet identified **100 legacy cases without `group_keys`** (smoke, route, and policy retrieval). Core and intent-dialogue cases also share customers/orders or templates within their development sets. A valid grouped locked split therefore needs case-group annotation and new independent fixture families; simply relabeling current development rows would leak source patterns across partitions. Two independent reviewers must accept or revise all critical gold and the assigned normal sample before a locked release result can be claimed. Reviewer names/decisions and adjudication remain blank in the ignored packet.

The source data contain synthetic fixtures only. Do not commit completed reviewer sheets if they include reviewer identities or notes. The packet generator and assignment test can be rerun with:

```text
.venv/bin/python scripts/prepare_minimum_review.py
.venv/bin/pytest -q tests/test_minimum_review_packet.py
```
