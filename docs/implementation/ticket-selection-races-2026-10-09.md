# Support ticket selection races — 2026-10-09

In the browser, a slow ticket-detail response could replace a newer ticket selection. A pending detail could also reopen a ticket after the agent selected a status filter that excluded it. Finally, an in-flight message action on ticket A could refresh A's detail after the agent opened ticket B. These races affected display state only; ticket authorization and message writes remained server controlled.

The UI now versions ticket-detail reads. A newer ticket selection or status filter invalidates pending detail responses. An action refreshes its ticket detail only if that same selection is still current when the action completes. Three held-response browser regressions each failed on the prior UI and passed after the corresponding guard.

The ticket UI suite passed **6/6**, and the combined adjacent ticket, actor-switch, chat-context and return-retry suites passed **18/18**. The production Next.js build and TypeScript check passed. No-key minimum run `20261009T002736Z-minimum-fbf521` passed **7/7 suites**, with **379 Python tests passed, five optional skips and nine Alembic warnings**; the locked release gate remains false.

Compose rebuilt and recreated the local web service and its API dependency. Web returned HTTP 200, API `/health` returned `{"status":"ok"}`, and four read-only real-Keycloak role views passed. The disposable mock browser server was stopped. No model-provider or Langfuse call was made. Wider accessibility and browser recovery checks remain open.
