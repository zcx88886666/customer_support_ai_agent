# Browser actor-switch containment, 2026-10-06

The mock browser view now clears customer-bound orders, chat, profile, tickets, proposals, deadline alerts, notices and return/form identifiers when its identity changes. The synthetic return ID remains available across customer→warehouse→supervisor role changes for the same actor, then clears when the actor changes. API calls capture an actor/role generation; stale successes and errors are ignored, including when a user switches away and then returns to the original actor. OIDC token refresh for the same subject and role keeps that generation. Backend authorization and refund controls were unchanged.

## Verification

- Three initial Playwright regressions reproduced the old order/chat response, return ID and error-notice leaks (**3 failed as expected**).
- The first fix passed four targeted browser tests, including the existing profile response race. A fresh read-only review found an A→B→A response gap and a weak notice assertion. One fix pass added a generation guard, a round-trip regression, and an actual notice assertion. Final targeted result: **5 passed**.
- The final Next.js production build passed, including TypeScript. The five browser tests were rerun after the before-paint reset change and passed again.
- The local Compose web and Keycloak endpoints on ports 3000/8080 were down during this slice, so the previous real-OIDC role/workflow browser replays were not repeated. The targeted tests used intercepted synthetic HTTP responses on a temporary mock-mode web server at port 3001. No provider or Cloud calls were made.

```bash
cd apps/web
NEXT_PUBLIC_AUTH_MODE=mock NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 npm run dev -- -p 3001 -H 127.0.0.1
ACTOR_SWITCH_TEST=1 PROFILE_RACE_TEST=1 npx playwright test tests/actor-switch.spec.ts tests/profile-switch.spec.ts
npm run build
```

The transient test server was stopped. The broader browser recovery/accessibility and independent real-OIDC replay remain open; this change does not establish a release gate.
