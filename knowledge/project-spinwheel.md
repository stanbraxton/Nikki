# SpinWheel — random name picker

SpinWheel is a web-based random name picker and decision-making utility designed for general users, educators, and creators who need a fair, engaging way to select winners or items from a list. It provides a visual spinning wheel experience with weighted entries, customizable animations, and interactive audio-visual feedback.

## Overview & Hosting History
- **Initial Setup**: Initially configured on the legacy hosting platform under project `spinwheel` (path reference `spinwheel-8f727d0a395f`) with public client-side auth mode (`[2026-08-14]`). Legacy preview and production environments were gated by workspace members.
- **Migration (2026-09-04, Nikki M2)**: Migrated off the legacy hosting platform to Stan's private GitHub repository (`https://github.com/stanbraxton/spinwheel`) and Firebase Hosting (`https://spinwheel-25137.web.app`, Firebase site `spinwheel-25137`, project `nikkiaia-prod`). The current application is a pure static Vite app without Convex or backend auth; the "Made with legacy platform" badge has been stripped.
- **Your Workflow (Nikki)**: When Stan tells you ("open stanbraxton/spinwheel, change X, commit and push, then deploy_app spinwheel"), you execute the repository updates and deployment. The legacy platform environment remains strictly as a legacy/fallback option.
- **Status**: Feature work is at Milestone 1; further milestones require Stan's explicit authorization.

## Architecture & Code Map
- **`src/lib/wheel.ts`**: Contains core calculation utilities including `parseEntries` (supports `Name*3` weight syntax up to a maximum of 1000 entries), `buildSegments`, `pickWinner` (weighted random selection), `computeTargetRotation` (winner is drawn *first*, then the wheel rotation is computed to land on it so visual output always matches the fair draw), and `easeOutQuint`.
- **`src/lib/sound.ts`**: WebAudio tick and fanfare generator; requires `primeAudio()` on the first user click to satisfy iOS audio policy requirements.
- **`src/lib/confetti.ts`**: Dependency-free canvas confetti utility.
- **`src/components/Wheel.tsx`**: Canvas wheel component utilizing `requestAnimationFrame` for a 6–8 turn spin over ~5.2 seconds (`data-testid="wheel-canvas"`).
- **`src/pages/SpinnerPage.tsx`**: Main page housing the entries textarea (`entries-input`), spin button (`spin-button`), winner dialog (`winner-dialog`), winner name display (`winner-name`), mute toggle, and "Remove & continue" functionality.
- **`scripts/spinner-test.ts`**: Playwright end-to-end test suite (passes successfully). Local test run command structure: `VITE_APP_ACCESS_MODE=public bunx vite build && bunx vite preview --port 4173`, followed by `APP_URL=http://localhost:4173 bun scripts/spinner-test.ts`.

## Data Model
- **Current Implementation**: Client-side state only; no backend database integration is active yet.
- **Competitor Reference Model (Wheel of Names Teardown, 2026-08-13)**:
  - `entries`: Objects containing `{ text, image, color, weight, enabled }`.
  - `config`: Objects containing `{ spinTime, hubSize, colorSettings, sounds, autoRemoveWinner, displayWinnerDialog, winnerMessage, isAdvanced }`.

## Competitor Reference (Wheel of Names Teardown, 2026-08-13)
- Built on Vue 3 + Quasar SPA hosted on Fastly.
- REST endpoints under `/api/v3/*` (wheels, wheels/shared, gallery, users, api-keys, client-settings, admin/*).
- Firebase backend (`wheel-of-names-firebase` with Google Auth + RTDB).
- Freestar ads.

## Status & Roadmap
- **Milestone 1**: Completed (`[2026-08-13/14]`).
- **Current Directive**: Stan has issued a strict stop order on further development until he re-engages. Do not initiate Milestone 2 unprompted.
- **Future Roadmap Milestones (Pending Stan's Go-Ahead)**:
  - **M2**: Customization (colors/themes, per-entry images, sounds, spin duration, advanced mode).
  - **M3**: Accounts + save/share.
  - **M4**: Gallery + moderation.
  - **M5**: Upgrades (AI wheels, multiplayer, analytics, CSV/Sheets import, embed + API, brackets, video reveals).

## Gotchas & Lessons Learned
- **iOS Audio Unlock**: Mobile browsers (especially iOS Safari) require an explicit user interaction via `primeAudio()` on the first click to unlock the WebAudio context.
- **Fair Draw Sequencing**: Computing the winning entry *before* calculating the target rotation angle is critical to maintain absolute synchronization between the fair random draw and the visual stopping point.
- **Testing**: Playwright e2e test suite (`scripts/spinner-test.ts`) successfully verifies local builds.

## Open Questions & Unresolved Items
- **Monetization & Ads**: Unresolved whether ads will be included (competitor teardown references Freestar ads).
- **Product Naming**: "SpinWheel" is currently a temporary placeholder name.
- **M5 Upgrades**: Selection of specific M5 upgrade features to implement remains undecided.
