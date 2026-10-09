# Web accessibility basics — 2026-10-09

At a 320px viewport the customer page was 338px wide because its grid column required 310px in addition to page padding. The page also had no explicit keyboard focus style or live status semantics for chat results and operation notices. Two new Playwright checks failed on the prior UI: horizontal overflow and a missing chat status region.

The web now lets the grid column shrink to the available width and uses responsive page padding. Keyboard focus has a visible three-pixel outline. Chat answers and successful operation notices expose polite status semantics; errors expose an alert. A third browser check covers a failed chat request. This changes presentation and announcements only; server authorization and business writes are unchanged.

The final six-suite mock browser group passed **22/22**, covering accessibility, tickets, actor switching, profile switching, chat context and return retries. The production Next.js build and TypeScript check passed in the offline Node/Playwright image. Compose rebuilt and recreated the local web and API dependency; web returned HTTP 200 and API `/health` returned `{"status":"ok"}`. The disposable mock web test container was removed. No model-provider or Langfuse call was made.

To rerun the focused test from the repository root with the version-matched cached image, start a disposable mock web service as described in [browser lost-response recovery](browser-lost-response-2026-10-08.md), then run:

```bash
docker run --rm --network host -v "$PWD/apps/web:/work" -w /work -e ACCESSIBILITY_UI_TEST=1 -e NEXT_TELEMETRY_DISABLED=1 mcr.microsoft.com/playwright:v1.63.0-noble ./node_modules/.bin/playwright test tests/accessibility.spec.ts --reporter=list
```

The checks establish DOM semantics, keyboard focus styling and small-viewport width. They are not an assistive-technology usability study. Broader focus flow, language and contrast review across all role journeys remains open.
