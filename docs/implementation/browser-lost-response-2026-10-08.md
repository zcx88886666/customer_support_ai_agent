# Browser return retry after a lost response (2026-10-08)

The customer return form already keeps one idempotency key while its order, item, quantity and reason remain the same. A browser network failure after submission previously displayed only `TypeError: Failed to fetch`, leaving the customer unsure whether the request was accepted. Direct, Agent, and explicit review submissions now show: “提交结果未确认。请在当前页面重试；系统会使用同一请求编号避免重复申请。” The form stays available so the customer can retry the same request without refreshing the page. The message does not claim that a return or refund happened.

The new Playwright case models a committed return whose first response is lost. It failed on the old message, then passed with the new message. It confirms the next click sends the same request key and displays the recorded return ID. The complete mock return browser suite passed **7/7**. These intercepted responses verify browser behavior; backend idempotency is checked separately by the domain and HTTP suites.

Three additional Playwright cases used the deployed OIDC web, real Keycloak login, and a disposable migrated PostgreSQL-backed API. For direct return, Agent return, and explicit review, the browser route forwarded the first authenticated POST to that API, read its committed return or ticket ID, then deliberately dropped the response before it reached the page. Each same-page retry sent the same key and returned the same ID; **3/3 cases passed**. Direct database readback after the final run found **two returns, one review ticket, two `create_return` audits, one review-ticket audit, and zero refund ledgers**. Every return and ticket belonged to `cust-01`. The disposable API and database were removed after verification; the live demo database was untouched.

The WSL host had no Node/npm or Linux browser. A version-matched `mcr.microsoft.com/playwright:v1.63.0-noble` image supplied both. A disposable mock-mode web server mounted `apps/web` on port 3001, and a second container ran the tests with host networking. The disposable server was stopped and removed after the run. The production web build passed in that container. Docker Compose rebuilt and recreated the local API and web; `GET /health` returned `{"status":"ok"}` and the web returned HTTP 200. No OpenRouter or Langfuse call was made.

To rerun the mock browser check from the repository root after pulling the version-matched image:

```bash
docker run --rm -d --name resolveai-browser-recovery-web -p 127.0.0.1:3001:3001 -v "$PWD/apps/web:/work" -w /work -e NEXT_PUBLIC_AUTH_MODE=mock -e NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 -e NEXT_TELEMETRY_DISABLED=1 mcr.microsoft.com/playwright:v1.63.0-noble npm run dev -- --hostname 0.0.0.0 --port 3001
docker run --rm --network host -v "$PWD/apps/web:/work" -w /work -e CHAT_RETURN_UI_TEST=1 -e NEXT_TELEMETRY_DISABLED=1 mcr.microsoft.com/playwright:v1.63.0-noble ./node_modules/.bin/playwright test tests/chat-return.spec.ts --reporter=list
docker stop resolveai-browser-recovery-web
```

The commands use the existing `apps/web/node_modules` tree. To run the real OIDC cases, create a fresh migrated and seeded OIDC API on local port 8003, then run `BROWSER_RECOVERY_API_URL=http://localhost:8003 ./node_modules/.bin/playwright test tests/return-recovery-oidc.spec.ts --workers=1` from `apps/web` with the version-matched browser runtime. The tests redirect only that browser's API calls to the isolated service; use a new database because they commit two returns and one review ticket. Run them with one worker so each case has a predictable fresh order. A test against a previously used database is not a valid replay of the first-submit path.
