# ResolveAI v6 completion roadmap

> Audited 2026-10-04 against [v6](../../plans/resolveai-v6.md), [verified status](../STATUS.md), and the [issue log](ISSUES.md). This is a work queue, not evidence of completion. Keep measured results in `STATUS.md` and dated reports.

## Current release position

The Docker/PostgreSQL/Keycloak/Commerce MCP application, controlled refund workflow, no-key startup, Prompt catalog, and observability paths run. The evidence is development evidence on synthetic data. `single` remains the default Agent mode because the 20-case live paired composite suite tied on success. No independently reviewed locked suite exists yet. Do not publish a production success rate from these runs.

| v6 evaluation suite | Verified development evidence | Minimum runnable set | Later v6 target | Remaining gate |
|---|---:|---:|---:|---|
| Smoke | 30 unique cases, 33 executions | 25 | 25 | Independently reviewed critical gold and a clean locked split |
| Core business | 6 isolated HTTP/worker cases, also replayed with OIDC/PostgreSQL | 30 | About 200 | 24 more distinct terminal workflows for minimum; group-safe split and human review |
| Intent/clarification | 30 route utterances | 30 | About 140 | Add slot, reference, clarification, and Replan gold; independent review |
| Policy RAG | 20 positive plus 20 near-negative published-demo queries | 20 | About 60 | Human relevance review; version and scope coverage beyond four demo clauses |
| Collaboration | 20 composite pairs, 20 single-domain pairs, 12 fault pairs | 20 | About 80 pairs | Real slow/contradictory sources; independent paired gold and safe grouped split |
| Memory | 40 story A/B | Separate 40-story target | About 40 | Retention decision and sustained mixed traffic; Mem0 stays offline after failing correction/deletion |
| Rule state | 2,000 generated sequences and targeted mutations | Separate 2,000–5,000 target | 2,000–5,000 | Broader transition families and measured mutation campaign; never count these as Agent cases |

The counts above describe existing development coverage, not interchangeable unique cases. They do not meet v6's review contract: two reviewers for every critical case and at least 20% of other cases, with adjudication. The intended `dev`/`locked` partition is grouped by customer, order, conversation source, and template family; paraphrases must stay together. The first integrity correction relabeled 11 author-written smoke rows from `locked` to `dev` and made the smoke loader reject a premature locked label.

## Ordered gates

1. **Evaluation contract and business minimum.** Expand the cross-role runner from six to at least 30 distinct terminal-state workflows. Add shared case validation and group keys, fixed clock, provenance, review fields, and a split generator that keeps related cases together. Preserve the gold from Agent input. Make reports show source, split, critical failures, incomplete cases, and version hashes. Replay the minimum set through isolated committed requests and worker actions; then use OIDC/PostgreSQL on critical workflows. A passing development set is an engineering checkpoint, not release acceptance.
2. **Independent gold and locked baseline.** Have reviewers adjudicate critical labels and the required sample of other labels. Freeze code, Prompt release, policy bundle, model, fixture, scorer, and dataset hashes before running locked cases. Run the same locked cases in `single` and `collab` modes with paired order randomization and identical fixtures. Keep `single` as default unless v6's safety, evidence, latency, and cost conditions are met. Reviewer participation is the first external dependency; the implementation can prepare the review packet and validation without it.
3. **Agent and retrieval hardening.** Add request-wide call/time/cost limits, provider/schema regressions, and real slow-service, contradiction, late-result, and unauthorized-tool integration. Expand policy retrieval to reviewed historical/version-scoped gold; calibrate abstention before enabling semantic embeddings. Keep the deterministic money and authorization gates authoritative.
4. **Operational proof.** Measure sustained mixed read/chat/write traffic and refund-worker concurrency, including worker death during a database transaction. Implement the planned Redis/Celery job path or record a dated approved design change. Copy a verified Git bundle to a separate offline medium, restore it, and test the retention/erasure policy for backups and exports. Improve browser error/recovery and accessibility, then rerun the four fixed demo journeys end to end.
5. **Observability and handoff.** Run a larger reviewed evaluation with Cloud Trace/Score export and local case reconciliation; capture the actual Langfuse billable-unit counter from Usage Management. Confirm each fixed demo can link local terminal state and audit to Jaeger and Langfuse. Finish repeatable README setup and deployment/rollback evidence, then update `STATUS.md` only with observed results. Cloud usage-tier confirmation and offline-medium/retention choices need external input.

Optional or conditional v6 items are Prometheus/Grafana, Mem0 online only after safety gates, and external corpora/benchmarks when licensing and original task contracts permit. Real payment integration, multi-tenant SaaS, and Agent-authorized refunds are outside the v6 scope.

## Immediate next work

Expand the six-case business runner by scenario family rather than order-ID or wording variants. Start with supervisor rejection, repeated approval/worker replay, partial quantities and cumulative rounding, wrong-role mutations, seven-day boundary instants, stale policy/amount, and receipt/inspection quantity exceptions. Each new case needs a database/audit gold state and a scorer mutation that proves the relevant failure is detected. Record the development result and the separate OIDC/PostgreSQL replay result before marking the minimum gate done.
