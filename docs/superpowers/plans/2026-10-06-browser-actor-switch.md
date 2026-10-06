# Browser actor switch containment plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent late customer or role responses from populating a different actor's browser view, and clear identity-bound content on actor changes.

**Architecture:** Tag each browser API call with the current mock actor/role or OIDC subject/role at request start. Reject a result or error if identity changes before it settles. Clear sensitive customer view state when the actor changes while retaining same-actor mock role handoff IDs for the demo workflow. Keep server-side ownership checks authoritative.

**Tech Stack:** Next.js React, Playwright intercepted synthetic HTTP responses.

**Spec:** v6 §§4,8,12; previously recorded frontend error/actor-switch gaps in STATUS and `docs/implementation/ISSUES.md`. User authorized autonomous completion. This changes the existing page only.

## Review focus

- A held order/chat/profile response from actor A must never display in actor B's view or set B's notice.
- A failed held response cannot expose A's error text to B.
- Switching customer actors clears prior return IDs and notices immediately; mock role switches for the same actor preserve the return ID needed for the demo.
- A same-actor request still renders normally; account token refresh for the same OIDC subject does not discard the response.
- No backend authorization or refund authority changes.

### Task 1: Contain browser response races

**Files:** Modify `apps/web/app/page.tsx`, create `apps/web/tests/actor-switch.spec.ts`, update README/STATUS/ISSUES and report.

- [x] Write a Playwright race test that holds an old actor's order/chat response, switches actor, and verifies old data/notice never appear; capture existing behavior as RED. Also cover old return ID clearing and same-actor successful data.
- [x] Implement request identity checks and central stale-error suppression, then run the test GREEN.
- [x] Build Next.js, run targeted browser tests and existing real OIDC role/workflow checks where available.
- [x] Obtain one fresh read-only final review; fix Critical/Important findings in one pass.
- [x] Record evidence, limitations, diff check and commit locally.

## Rulings

- Ruling: Keep the synthetic mock same-actor role handoff return ID because customer→warehouse→supervisor is an intentional local demonstration. Clear it when actor changes; OIDC uses separate authenticated browser sessions. Cost if wrong: an actor who manually switches roles within one mock session sees the same synthetic return ID, with backend authorization still enforced.
- Ruling: Increase a request generation on every actor or role transition. Returning to an earlier actor/role must not admit requests from before the intervening transition. Same-subject OIDC token refresh does not change the generation.
