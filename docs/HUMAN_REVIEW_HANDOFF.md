# Human review and input needed for the v6 release gate

> Current state on 2026-10-09 UTC: the no-key development minimum passes, but the independently reviewed locked release gate is false. See [status](STATUS.md), [review criteria](implementation/eval-review-criteria-v3.md), and [v6 evaluation requirements](../plans/resolveai-v6.md#9-评估业务终态协作增益与反向测试).

## 1. Independently review the evaluation cases

The current ignored local packet is `evals/review_packets/20261009T152716Z/`. Its `review_cases.jsonl` and `manifest.json` describe **166** synthetic cases, including **73 critical** cases. `reviewer_a.csv` assigns all 166; `reviewer_b.csv` assigns 92, including every critical case and at least 20% of normal cases. There are **zero completed human reviews**. Earlier LLM reviews are advisory and do not fill either human sheet.

Assign two independent people. Each should check the source fixture and the v6 rules before seeing the other's sheet or model suggestions, record a decision with a reason and UTC time, and provide a complete corrected gold object for a `revise`. Resolve disagreements in `adjudication.csv` only after both sheets are complete. The packet README gives the field instructions. Then run:

```bash
PYTHONPATH=apps/api:. .venv/bin/python scripts/validate_minimum_review.py evals/review_packets/20261009T152716Z
```

`forms_complete` validates form structure and source hashes; it does not prove independent judgment or correct gold. If source cases change before review, regenerate the packet and review the new version.

## 2. Create genuinely independent case families

The existing cases have one connected customer/order/source/template group per suite. A leakage-safe 30–50% locked split therefore cannot be made from this packet, even after every review form is complete. New source scenarios and fixtures need distinct group keys and should cover the underrepresented multistep, exception, contradictory-evidence, and policy cases. Engineering can draft them; people must review the scenario truth and gold before they become locked. Renaming IDs or rewording the same template does not create independent cases.

After review and adequate independent families exist, run:

```bash
PYTHONPATH=apps/api:. .venv/bin/python scripts/prepare_grouped_split.py <completed-packet-directory>
```

The output is only a `locked_candidate` dataset. Confirm the reviewer independence and leakage grouping outside the script, freeze code, scorer, dataset, policy bundle, Prompt release/hash and pinned model, then run the actual isolated locked comparison and check database/audit safety. The current packet is expected to fail the split command.

## 3. Confirm owner decisions before broader claims

| Owner input | Why it is needed |
|---|---|
| Policy owner approves any expanded policy corpus and adjudicates relevance/near-negative gold | The published demo has four clauses. The experimental broader lexical set performed poorly, and the semantic candidate lacks a safe abstention gate. Unreviewed policy text must not be published as authoritative. |
| Business owner defines manual outcomes for damaged goods, receipt disputes, complex payments and support tickets | The application can create and track the handoff, but it cannot infer a binding adjudication or supervisor approval from a model answer. |
| Data/security owner sets backup location, retention and deletion rules | Disposable restore drills pass, but no offline copy of the current stack or historical-erasure policy is established. |
| Project owner checks Langfuse's actual tier, billable-unit count and retained access window before a larger Cloud run | The local aggregate is only a proxy. Normal development evaluation and this follow-up can run without Cloud export. |
| Human testers inspect assistive-technology behavior and the intended deployment environment | Automated browser checks and local OIDC tests cannot certify screen-reader usability or production security. |

Optional live-model or large-load sweeps need an agreed spend and capacity window. The no-key minimum and queued development suites need neither.
