# Customer language preference browser check — 2026-10-03

The customer view now exposes the authenticated [profile API](../../apps/api/resolveai/api.py) for viewing memory consent and saved language, explicitly consenting, confirming a Chinese or English language value, deleting that value, and withdrawing consent. The UI sends `confirmed=true` only when the customer clicks the save button. It displays that language changes wording only. Switching the demo actor or role clears the locally displayed profile to avoid showing the previous account's preferences.

The Next.js production build compiled and passed TypeScript checks. The [Playwright OIDC test](../../apps/web/tests/language-preference.spec.ts) then passed against the rebuilt Docker web/API stack: customer-one signed in, enabled consent if needed, saved English, received an English response for an owned undelivered package, deleted the preference, received a Chinese response on the next chat, and restored the original consent and language using authenticated API requests in `finally`. The test used only synthetic demo accounts and did not submit a return or refund. Result: **1/1 browser test passed**; API health remained healthy.

The first browser attempt could not launch because Playwright's bundled Chromium was absent. Its CDN download timed out. A previously installed local Chrome existed at `/tmp/resolveai-chrome/opt/google/chrome/chrome`; the first launch of that binary lacked `libnspr4.so`. Using the existing `/tmp/resolveai-chrome-libs/usr/lib/x86_64-linux-gnu` library directory resolved it. These are local test-environment dependencies, not app runtime dependencies.

Reproduce with a local Chrome and its runtime libraries available:

```bash
cd apps/web
PATH=/tmp/resolveai-node/bin:$PATH npm run build
LD_LIBRARY_PATH=/tmp/resolveai-chrome-libs/usr/lib/x86_64-linux-gnu CHROME_TEST_PATH=/tmp/resolveai-chrome/opt/google/chrome/chrome PATH=/tmp/resolveai-node/bin:$PATH npx playwright test tests/language-preference.spec.ts
```

The browser test uses real OIDC and the existing synthetic Docker demo. It restores the original profile but leaves the new synthetic chat thread records for trace review. It does not establish memory write throughput or implement free-text communication-style rendering.
