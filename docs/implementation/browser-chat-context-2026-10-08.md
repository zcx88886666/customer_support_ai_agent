# Browser chat context after an order change — 2026-10-08

A customer could send a chat question for one order, change the order field while the request was pending, and then see the earlier order's answer and status under the new order. The existing actor-switch guard did not cover changes within one customer session.

The browser now versions pending chat reads and clears the displayed answer when the selected order or package changes. An older chat success or error cannot update the answer or notice after that context change. The server still checks order ownership and retains its own safe thread/replan behavior.

The synthetic browser regression held an order-01 chat response, changed the input to order-02, then released the response. It failed before the fix because `OLD_ORDER_ANSWER` appeared, and passed after the fix. The neighboring actor-switch and return-retry suites passed **12/12** with the new case. The production Next.js build and TypeScript check passed. The no-key minimum run `20261008T235802Z-minimum-d76b96` passed **7/7 suites**, including **379 Python tests passed, five optional skips and nine Alembic warnings**; its locked release gate remains false.

Compose rebuilt and recreated the local web service and its API dependency. The web returned HTTP 200, API `/health` returned `{"status":"ok"}`, and four read-only real-Keycloak browser role views passed. The mock test server was removed. These checks made no OpenRouter or Langfuse request. Wider frontend recovery and accessibility checks remain open.
