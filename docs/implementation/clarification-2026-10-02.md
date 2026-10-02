# Clarification state verification — 2026-10-02

Chat tasks now retain a server-side task ID, route and safe return slots while awaiting clarification. A pending clarification expires after 24 hours without a reply. A new completed task starts at plan revision 1 with a fresh checkpoint key, so repeated questions on one thread do not exhaust the four-revision budget or replay prior findings. A changed order clears the old item, quantity and reason. Unknown intent gets one clarification; another unknown response creates a support ticket. At most two unanswered clarification prompts are issued per task, while a complete third-turn return submission can proceed only with fresh explicit confirmation. A legacy pending return state without the new route field remains resumable.

Verification:

```text
.venv/bin/pytest -q tests/test_agent.py   12 passed
.venv/bin/pytest -q                       36 passed (outside restricted sandbox)
evals/runners/run_smoke.py                25 unique cases, 28/28 executions passed
scripts/verify_oidc.py                    seven authenticated Keycloak/API/MCP checks passed
```

The first smoke run after treating unknown messages as clarification had one failure: the greeting `你好` was expected to receive a normal reply. It now has an explicit greeting route. The later smoke run passed all 28 executions. The runner used the deterministic no-key mode; the OIDC check exercised one authenticated model/MCP chat after rebuilding the Docker API.

The bounded order/policy version Replan work was added later the same day; see [its verification report](replan-2026-10-02.md). This report covers the clarification budget and task checkpoint scope.
