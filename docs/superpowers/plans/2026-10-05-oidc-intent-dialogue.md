# Real OIDC multi-turn dialogue implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replay the existing twelve development dialogue cases through real Keycloak JWT authentication, HTTP MCP and PostgreSQL checkpoints.

**Architecture:** Reuse the unchanged intent dialogue scorer and dataset. A Docker-owned synthetic PostgreSQL server holds one freshly migrated database per case; each case starts private loopback API/MCP processes with all provider and Cloud keys disabled. Capture SQL counts after every real HTTP turn and authoritative terminal assertions, keeping failed databases/logs while removing successful owned resources.

**Tech Stack:** Existing httpx/OIDC harness, SQLAlchemy/Alembic, PostgreSQL, stateless MCP and LangGraph parent saver.

**Spec:** v6 §§3,5,9–10 and remaining real multi-turn coverage in STATUS. User authorized autonomous implementation; this extends the existing twelve-case evaluation flow. Native execution continues on local master.

## Constraints and review focus

- No changes to author-written expected labels, no assertion weakening; pending human review remains pending and release_gate_pass stays false.
- One fresh migrated database and checkpoint namespace per case; no broker messages, no late process contaminating another fixture.
- Genuine customer bearer token for API/MCP, never mock headers. Disable paid inference/Cloud exports explicitly.
- Reject unsupported customer mapping; raw answers and credentials stay out of summary/HTML. Terminal scorer must include per-turn count/route/revision and ledger/audit/ownership checks.
- Case errors/cancellation count incomplete, preserve full reports and stopped failed fixture, and kill/reap private children before cleanup.

### Task 1: Real transport replay

**Files:** Create `scripts/verify_intent_dialogue_oidc.py`, `tests/test_intent_dialogue_oidc.py`, implementation report; update README/STATUS/ISSUES. Reuse existing scorer and fixture facts; no production behavior changes planned.

- [x] Write failing contract regressions for explicit customer mapping, chronological additional package fixture, per-turn/terminal scoring and cancellation summary. Expected: fail before verifier helper exists.
- [x] Implement bounded private process replay, safe reports and guarded fixture cleanup. Expected: focused contract tests pass.
- [x] Run all twelve real OIDC/PostgreSQL dialogue cases and full Python suite. Expected: twelve complete passes or concrete production defects reproduced and fixed with red→green tests.
- [ ] Obtain one fresh read-only final review, fix Critical/Important findings in one pass.
- [ ] Record actual results and limitations, diff check and explicit local commits.

## Rulings

- Ruling: Preserve the same synthetic development dataset and scorer across mock and real transport — expose service/state differences without pretending fresh labels are independent gold. Cost: twelve cases remain author-written development coverage.
- Ruling: Disable provider and Cloud calls for this transport slice — real JWT/MCP/SQL/checkpoint behavior is independently measurable. Broader real-model/locked evaluation remains a separate gate.

- Ruling: Restart the private API after turn1 in each multi-turn case to test durable state rather than process-local continuity; nine restarts passed with unchanged labels. No production changes were needed.
