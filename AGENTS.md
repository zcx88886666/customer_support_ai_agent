# Repository Guidelines

## Project Structure & Module Organization

This repository is design-only: no application code, tests, or deployment assets exist yet. `docs/PROJECT_CONTEXT.md` summarizes approved design decisions; `docs/STATUS.md` records verified progress. `plans/resolveai-v6.md` is the current after-sales specification; earlier `resolveai-v*.md` files and `resolveai-v3-evaluation-spec.md` preserve history. `plans/commerceguide-presales-v1.md` describes a separate project. The v6 plan proposes `apps/`, `services/`, `packages/`, `prompts/catalog/`, `data/`, `evals/`, and `infra/`; create these directories as their first real contents are implemented.

## Build, Test, and Development Commands

There is no install, build, run, lint, or test command yet. Use `rg --files plans` to list specifications and `rg -n '^## ' plans/resolveai-v6.md` to navigate the active plan. When implementation begins, document reproducible Ubuntu setup, no-key mock startup, and verification commands in `README.md` before presenting the system as runnable.

## Coding Style & Naming Conventions

Keep Markdown headings descriptive and links relative. Preserve older plan versions; record substantial design changes in a new version or a clearly dated decision. For future Python code, use four-space indentation and `snake_case`; for TypeScript, use two-space indentation, `camelCase` identifiers, and `PascalCase` React components. No formatter or linter is configured yet. Store editable prompt templates only as `prompts/catalog/<name>.yaml`, as specified in v6.

## Testing Guidelines

No test framework or coverage threshold is established. Add tests alongside each implemented slice: Python `test_*.py`, web `*.spec.ts`, and versioned evaluation cases under `evals/`. Prioritize ownership checks, seven-day boundaries, approval-before-refund, idempotency, synthetic-data consistency, and trace/evaluation reconciliation. Report commands and actual results; do not describe planned targets as measured outcomes.

## Commit & Pull Request Guidelines

This directory has no Git history, so no commit convention can be inferred. Once Git is initialized, use concise `type(scope): summary` messages, such as `docs(plan): clarify refund approval`. Pull requests should state the change, relevant v6 section, validation performed, and data/security impact; include UI screenshots or evaluation reports when applicable.

## Security & Agent-Specific Instructions

Read `docs/PROJECT_CONTEXT.md` and `docs/STATUS.md` before planning, then the relevant v6 sections for detail. Update context for approved decisions and status only for verified work; never infer implementation from a plan or chat history. Never commit credentials, `.env` values, restricted source data, million-row generated outputs, or unredacted traces. Agents must not treat model output as authorization for refunds or access to another customer's orders.
