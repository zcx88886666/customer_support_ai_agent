# Real OIDC multi-turn dialogue replay, 2026-10-05

The existing twelve `intent_dialogue_dev_v1` development cases now have a real transport verifier. Each case receives a newly migrated database inside an owned disposable Docker PostgreSQL server and private loopback API/MCP processes. Customer requests use actual Keycloak code+PKCE tokens. The verifier reuses the unchanged dataset and scorer, records database counts after every HTTP turn, and checks final ownership and exact action audits.

The API process restarts after the first turn of every multi-turn case. Pending return slots, confirmation, selected-package clarification, order changes, unknown-intent limits and completed follow-ups therefore cross a process boundary. Every case also rejects mock identity headers and another customer's attempt to resume its thread. Additional package fixtures include causal shipped/delivered events. Provider and Cloud keys are disabled, and the runtime Prompt release matches the manifest even when the caller sets no environment override.

## Actual verification

- Initial run `20261005T082533Z-dialogue-oidc-bdf646`: **12/12 cases**, then removed the successful owned PostgreSQL server/volume.
- Final default-config run `20261005T082709Z-dialogue-oidc-41c04a`: **12/12**, **293/293 checks**, **23 HTTP dialogue turns**, **nine API restarts**, and **nine critical development cases**. No incomplete cases or critical failures. Two expected owned returns, zero ledger entries, and no paid model calls. Successful owned resources were removed; private API/MCP children were reaped.
- Six focused contract regressions passed: supported account mapping, package chronology, incomplete/cancellation summary, redacted turn artifacts, explicit no-key/release environment, and rejection of changed per-turn status/ledger counts by the shared scorer. They failed before their helpers were implemented.
- Full Python suite: **261 passed, four optional PostgreSQL/Docker skips**, in25.19seconds. No production code, Prompt catalog or dataset labels changed.

One fresh read-only review is pending.

```bash
.venv/bin/python scripts/verify_intent_dialogue_oidc.py
.venv/bin/python -m pytest tests/test_intent_dialogue_oidc.py -q
```

Run the verifier on the host with Docker, the cached PostgreSQL image and the README's local Keycloak demo accounts available. It writes ignored `manifest.json`, `case_results.jsonl`, `summary.json`, HTML and process logs. Failed/cancelled runs retain a stopped owned server/volume. Raw answers and bearer tokens are omitted from published report rows.

These are author-written synthetic development cases with human review still pending. The real services and restart behavior establish transport/state coverage; they do not turn these labels into independent gold, prove a locked model comparison, or close broader dialogue and real-model evaluation gates. `release_gate_pass` remains false.
