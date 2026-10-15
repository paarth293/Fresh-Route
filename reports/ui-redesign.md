# LastWindow interface redesign

The interface now uses a warm, light operations workspace with a forest-green navigation rail, lime accents, clearer typography, consistent icons, and deliberate spacing. The redesign is implemented in the actual React application and production bundle.

## What changed

- Rebuilt the shared application shell, navigation, scenario controls, status messages, and dispatch actions.
- Replaced the plain overview with an allocation network, stock and contribution metrics, a shelf-life watchlist, decision readiness, and an economic breakdown.
- The network uses real scenario destinations, nominal travel durations, and planned quantities. Its diagram is explicitly schematic, with working zoom, selection, and reset controls. Mobile provides readable destination rows alongside it.
- Added inventory search across batch identifiers, commodity identifiers, and commodity names. Tables include clearer states and useful empty results.
- Restyled dispatch, disruption, inspection, evaluation, receipts, evidence panels, and the password sign-in screen.
- Added keyboard focus indicators, reduced-motion support, readable text contrast, and responsive layouts. Existing API workflows remain connected.
- Added a local vector brand icon and page metadata; no remote images or fonts are required.

## Previews

- [Desktop overview](ui-overview-desktop.png)
- [Inventory](ui-inventory-desktop.png)
- [Mobile overview](ui-overview-mobile.png)
- [Overview before planning](ui-overview-empty.png)

## Verification

- Production build and TypeScript: passed.
- Frontend unit tests: 9 passed.
- Normal browser suite: 6 passed against the real backend, including the new network controls and inventory search.
- Password-protected browser suite: 1 passed.
- After final contrast and diagram-label adjustments, the desktop overview and mobile/analysis checks were rerun successfully. No horizontal page overflow was found at the tested desktop and 390 px mobile widths.
- Backend algorithms and APIs were not changed in this redesign; their prior verification is recorded in [the build report](build-verification.md).

Primary source files: `frontend/src/App.tsx`, `frontend/src/theme.css`, and `frontend/src/main.tsx`. The compiled application is served by the local FastAPI service. Use the project README startup instructions to view it.
