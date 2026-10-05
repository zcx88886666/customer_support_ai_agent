# Bounded Specialist Tool Planning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement actual model selection of read-only order tools and scoped policy queries under the existing request budget.

**Architecture:** Each specialist plans before reading evidence. The model can choose tool names or bounded query text; runtime closures supply all authenticated order references and immutable policy bundle IDs. A separate committed development prompt artifact enables planning while historical releases preserve their baseline behavior.

**Tech Stack:** Python, Pydantic, LangGraph, PostgreSQL, Commerce MCP, local PromptRegistry, OpenRouter GPT-4o-mini.

**Spec:** [ResolveAI v6 §§3.1–3.3, 6](../../../plans/resolveai-v6.md). This slice implements specialist planning; natural-language synthesis and semantic retrieval remain separate work.

## Global Constraints

- Each specialist performs at most two read-only tool calls per dispatch.
- The shared request budget retains a hard limit of ten LLM attempts, including retries.
- Models cannot supply customer identity, order authorization, another bundle ID, a write operation, approval, or money.
- Editable prompt content exists only in `prompts/catalog/*.yaml`; committed manifests verify its hashes.
- No-key mock execution and existing immutable release artifacts remain usable.
- Development checks do not establish independently reviewed locked gold or release readiness.

## Review Focus

- A model asks for another customer's order or a write tool: strict plan schema rejects it and runtime never forwards model arguments.
- A status-only query selects one order tool: return a verified order status without inventing a shipment.
- A shipment question selects insufficient tools: expose an evidence gap, never a fabricated shipping result.
- Policy rewrite redirects an unrelated question: original scope gate prevents retrieval; bundle selection remains server-owned.
- Parallel plan/model work overlaps: ORM reads retain the reviewed shared-session lock and actual tool counts stay at two or fewer.

## Task 1: Planning contracts and local prompt artifact

**Files:** Create `apps/api/resolveai/specialist_planning.py`, `tests/test_specialist_planning.py`, `prompts/catalog/order_tool_plan.yaml`, `prompts/catalog/policy_retrieval_plan.yaml`; modify `apps/api/resolveai/schemas.py`, `packages/agent/models-mock-v1.json`.

**Interfaces:** `plan_order(task, release_id) -> OrderToolPlan`; `plan_policy(task, release_id) -> PolicyRetrievalPlan`; `enabled(release_id) -> bool`. Order plans contain only unique `get_order`/`track_shipment` names, length 0–2. Policy plans contain only unique nonempty query strings, length 0–2 and at most 200 characters each. `DelegationTask.order_read_scope` defaults to legacy `shipment` and permits `status`.

- [x] Add schema/authorization tests, verify missing contracts fail.
- [x] Implement strict schemas, local prompts, and economical task configuration. Technical model unavailability uses deterministic read plans; valid empty plans expose missing evidence.
- [x] Verify schema rejection, duplicate limits, no-key defaults, technical fallback, exact release propagation, and original policy-scope abstention.
- [x] Commit catalog and contracts, then generate a new manifest with explicit development verification scope after committed content exists.

## Task 2: Execute bounded choices in both specialist graphs

**Files:** Modify `apps/api/resolveai/agent.py`, `apps/api/resolveai/commerce_client.py`; extend `tests/test_specialist_planning.py`, `tests/test_agent.py`.

**Interfaces:** `read_selected_order_tools(token, order_id, tools) -> dict[str, object]` binds runtime-only arguments. Findings retain actual tool counts, current versions, and independently validated sources. Status findings may cite only the owned order; shipment findings still require the selected owned package.

- [x] Add failing tests for one-tool status, selected-tool authenticated calls, invalid/empty/incomplete selection, bundle isolation, bounded second policy query, and concurrent planning with serialized local reads.
- [x] Add explicit graph planning nodes and scoped tool dispatch. Snapshot ORM evidence under the shared lock, release before model/MCP calls, and preserve historical-release behavior.
- [x] Extend merge validation and status answer assembly without weakening shipment checks.
- [x] Run focused tests and no-key minimum, record outcomes, commit.

## Task 3: Development rollout and real-model checks

**Files:** Modify `infra/compose/compose.yaml`, documented runner manifests, `README.md`, `docs/STATUS.md`, `docs/implementation/ISSUES.md`; create a specialist planning evidence report.

- [x] Let Docker select the explicit development artifact; run a bounded GPT-4o-mini paired sample with accurate actual release hashes and provider usage.
- [x] Run real-OIDC/PostgreSQL MCP and fault checks, rebuild the API, and verify all seven authorization checks.
- [x] Obtain focused independent review through the requesting-code-review skill and address substantiated findings.
- [x] Commit verified implementation and report remaining synthesis/semantic/human-review work honestly.

## Execution ledger

Baseline: committed `00093c0`, no-key minimum 7/7 with 151 Python tests, isolated PostgreSQL timeout test passed separately. No implementation agents are used; native execution follows the user's request to continue autonomously.

Ruling: continue in the shared checkout on master, following the user's explicit session instructions to commit to master and continue without questions. Changes are committed by slice; remote pushes remain separate. Cost if wrong: the work is on the shared local branch rather than an isolated branch.

Ruling: a development Prompt artifact records `verification_scope=development` and `locked_release_pass=false`; its `verified` flag means startup hash validation, not completion of independent locked evaluation. This preserves runtime compatibility while making the verification scope explicit. Cost if wrong: a consumer that ignores the scope could misread the existing flag as a release gate.

Task 1: missing-module test failed before implementation; strict schema/planner tests are implemented, together with two economical task entries and new catalog prompts. Existing immutable release-v1 hashes are unchanged.

Task 1 complete at `c526de6`: 20/20 planner/prompt/provider tests. Development manifest generated from that committed catalog.

Task 2: six integration tests went red→green. Initial no-key status-scorer failures and live queued-task timeout are retained in the [evidence report](../../implementation/specialist-tool-planning-2026-10-05.md). The scorer's status-source regression and serial scheduling regression went red→green; final no-key 7/7 passed.

Ruling: sequential dispatch deadline covers both queued tasks (at most twenty seconds), each invocation still gets ten seconds, and total request admission stays at twenty-five seconds by default. Parallel deadlines remain ten seconds. This reflects actual serial scheduling. Cost if wrong: a slow serial answer can take longer before reporting partial evidence, within the configured request account.

Task 3: real transport matrix 8/8 with 84 checks; targeted paid replay 6/6 at $0.000819; independent focused review 63 tests and one Important finding. Final fix: two-tool status display regression failed in both modes, then passed after using requested scope; no-key suite green with 175 tests.

External block: automatic approval review rejected the new Langfuse mirror upload twice, including after a clean generic-payload audit. Explicit user approval of this specific development artifact is required; no workaround attempted. This does not block local prompt inference.

Task 2–3 complete: the final no-key minimum `20261005T003122Z-minimum-37202e` passed 7/7 with 175 Python tests, and the final rebuilt Docker API passed all seven real authorization checks. Earlier failed runs are retained locally. A new 162-case human-review packet preserves changed metadata without copying previous model decisions. No deferred minor findings were reported. Broader v6 work and the rejected Cloud upload remain separately open.
