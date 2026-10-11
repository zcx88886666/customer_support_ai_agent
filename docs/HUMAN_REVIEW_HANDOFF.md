# Human review and input needed for the v6 release gate

> Current state on 2026-10-11 UTC: the no-key development minimum passes, but the independently reviewed locked release gate is false. The user can provide one human review. See [status](STATUS.md), [review criteria](implementation/eval-review-criteria-v3.md), and [v6 evaluation requirements](../plans/resolveai-v6.md#9-评估业务终态协作增益与反向测试).

## 1. Complete the available one-human review

The current ignored local packet is `evals/review_packets/20261011T025432Z/`. Its `review_cases.jsonl` and `manifest.json` describe **180** synthetic cases, including **84 critical** cases and 14 cases from two separate [review stories](implementation/single-human-review-pilot-2026-10-11.md). `reviewer_a.csv` assigns all 180. There are **zero completed human reviews**.

Check each case against its source fixture and the [review criteria](implementation/eval-review-criteria-v3.md) before looking at model suggestions. Fill **only `reviewer_a.csv`** with your identity, `accept`/`revise`/`reject`/`needs_context`, a concrete reason, and a UTC review time. For `revise`, supply the complete corrected gold JSON. Leave `reviewer_b.csv` and `adjudication.csv` blank; they are retained for a future second independent reviewer. The packet README gives the field instructions. Then run:

```bash
PYTHONPATH=apps/api:. .venv/bin/python scripts/validate_single_human_review.py evals/review_packets/20261011T025432Z
```

`single_review_complete` validates the first human form and source hashes. It does not prove gold correctness, make model advice a second human review, or pass the v6 locked release gate. If source cases change before review, regenerate the packet and review the new version. Earlier LLM reviews covered an older packet and are advisory only; a new model review may be run **after** your first pass to surface disagreements.

## 2. What remains for a v6 locked release

The two new source stories add distinct group components in every suite, but they contribute only 14 of 180 cases. The current packet still cannot meet the v6 30–50% leakage-safe locked split. Further independent multistep, exception, contradictory-evidence, and policy stories need authored source facts and reviewed gold. Renaming IDs or rewording the same template does not create independent cases.

The original v6 gate also requires a **second independent human** on all critical cases and at least 20% of normal cases, then adjudication. `reviewer_b.csv` currently assigns 104 cases. If that reviewer becomes available and sufficient independent case families have been added, run the full validator and candidate preparer:

```bash
PYTHONPATH=apps/api:. .venv/bin/python scripts/validate_minimum_review.py <completed-packet-directory>
PYTHONPATH=apps/api:. .venv/bin/python scripts/prepare_grouped_split.py <completed-packet-directory>
```

That output is only a `locked_candidate` dataset. Confirm reviewer independence and leakage grouping outside the script, freeze code, scorer, dataset, policy bundle, Prompt release/hash and pinned model, then run the isolated locked comparison and check database/audit safety. The current packet is expected to fail the split command.

## 3. Confirm owner decisions before broader claims

| Owner input | Why it is needed |
|---|---|
| Policy owner approves any expanded policy corpus and adjudicates relevance/near-negative gold | The published demo has four clauses. The experimental broader lexical set performed poorly, and the semantic candidate lacks a safe abstention gate. Unreviewed policy text must not be published as authoritative. |
| Business owner defines manual outcomes for damaged goods, receipt disputes, complex payments and support tickets | The application can create and track the handoff, but it cannot infer a binding adjudication or supervisor approval from a model answer. |
| Data/security owner sets backup location, retention and deletion rules | Disposable restore drills pass, but no offline copy of the current stack or historical-erasure policy is established. |
| Project owner checks Langfuse's actual tier, billable-unit count and retained access window before a larger Cloud run | The local aggregate is only a proxy. Normal development evaluation and this follow-up can run without Cloud export. |
| Human testers inspect assistive-technology behavior and the intended deployment environment | Automated browser checks and local OIDC tests cannot certify screen-reader usability or production security. |

Optional live-model or large-load sweeps need an agreed spend and capacity window. The no-key minimum and queued development suites need neither.
