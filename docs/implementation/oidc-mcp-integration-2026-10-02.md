# OIDC and Commerce MCP integration — 2026-10-02

The local Docker stack now runs Keycloak, API, web, and Commerce MCP with `AUTH_MODE=oidc`. Five synthetic role accounts were generated in Keycloak. Their passwords are stored only in ignored `.local/demo-accounts.json` with mode `600`; the bootstrap password remains in ignored `.env`. The persistent `keycloakdata` Docker volume retains the realm and accounts across container recreation. The provisioning script can be rerun without creating duplicate accounts.

The web client's access token carries separate audiences for `resolveai-api` and `http://localhost:8001/mcp`; API and MCP each verify the corresponding audience, issuer, signature, expiry, and role. The MCP server maps a customer to the server-controlled `customer_id` claim. The order specialist's OIDC path invokes read-only `get_order` and `track_shipment` via Streamable HTTP MCP with the signed customer token. Its tool names and call count are bounded, and it safely returns incomplete evidence when MCP fails. The token remains in a request-local closure and does not enter LangGraph checkpoint state. The mock mode still supports no-key development.

Verification performed:

| Check | Result |
|---|---|
| `.venv/bin/pytest -q` | 31 passed, including the routing regression. |
| `scripts/verify_oidc.py` | Seven checks passed: mock headers rejected, two code+PKCE customer logins, agent call through authenticated MCP, cross-customer API denial, role isolation, direct MCP reads, and MCP customer/role denials. |
| Next.js production build | Passed after selecting the stable TypeScript compiler API for this WSL environment. |
| Playwright Chromium | Four role browser journeys passed: customer, support, warehouse, supervisor. |
| Undelivered parcel question | Added a deterministic intent gate so a live model's `return_request` label does not turn “包裹没到能退吗” into a return submission. Targeted test verifies two read-only findings and the missing-delivery explanation. |

Initial Playwright runs timed out because the test used `127.0.0.1:3000` while Keycloak used `localhost:8080`; using `localhost` for both fixed browser sign-in. The Playwright Chromium CDN download timed out repeatedly. An official Chrome Debian package was downloaded and extracted under `/tmp`, with only three missing libraries also extracted under `/tmp`, then used through `CHROME_TEST_PATH`. No browser package was installed system-wide.

This integration does not establish specialist model reasoning, full graph approval interrupt/resume, larger locked evaluations, or production-grade identity deployment. Local Keycloak is intentionally a development configuration with synthetic users and loopback ports.
