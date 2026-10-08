# MCP shipment ownership before model review — 2026-10-08

The order specialist previously passed a selected Commerce MCP shipment into optional model evidence review before the coordinator checked that the shipment belonged to the selected order. The coordinator rejected a foreign finding later, but that was too late to protect the review boundary.

The specialist now checks the selected shipment ID against the locally owned order before review. A missing or foreign shipment produces a `conflict` finding without facts or source IDs; the existing bounded replan and handoff path handles it. The authenticated MCP service still enforces ownership on its own reads, and the coordinator retains its independent finding validation.

The new regression substituted a synthetic shipment belonging to another customer in an otherwise valid MCP response. It failed before the fix because the specialist returned `ok`; after the fix it returned `conflict` and the evidence reviewer was not called. Focused Agent/planning tests passed **76/76**. The no-key minimum run `20261008T234637Z-minimum-244203` passed **7/7 suites**, with **377 Python tests passed, five optional skips and nine Alembic warnings**. Its locked release gate remains false pending independent review.

The local Docker API image was rebuilt and its container recreated. `GET /health` returned `{"status":"ok"}`. The existing real Keycloak/OIDC/Commerce MCP smoke passed all seven authorization checks. These checks used synthetic demo data and made no model-provider or Langfuse request. The regression uses a substituted MCP response; it does not assert that the real MCP service emitted a foreign shipment.
