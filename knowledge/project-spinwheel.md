# SpinWheel — random name picker

SpinWheel is a web-based random name picker and decision-making utility designed for general users, educators, and creators who need a fair, engaging way to select winners or items from a list. It provides a visual spinning wheel experience with weighted entries, customizable animations, and interactive audio-visual feedback.

## Summary
The application is a React/Vite + Convex web app (currently hosted on the old platform; slated for migration to Stan's own GitHub + Firebase Hosting + Convex account). The auth mode is currently public with client-side-only handling and no backend data persisted yet.

## Business & pricing
- **Target Users**: General users, educators, and creators.
- **Monetization**: Unresolved. Stan has not yet answered whether the app will feature ads (the competitor teardown references Freestar ads) or other monetization models. "SpinWheel" remains a placeholder name.

## Architecture
- **Tech Stack**: React, Vite, Canvas-based wheel rendering, WebAudio API, and dependency-free canvas confetti.
- **Hosting & Deployment**: Currently hosted on the legacy platform (preview and production environments available, gated by workspace members) [2026-08-14], with a planned migration to Stan's GitHub + Firebase Hosting + Convex account.
- **Source Structure & Components**:
  - `src/components/Wheel.tsx`: Canvas wheel component, utilizing `requestAnimationFrame` for a 6–8 turn spin over ~5.2 seconds (`data-testid="wheel-canvas"`).
  - `src/pages/SpinnerPage.tsx`: Main page housing the entries textarea (`entries-input`), spin button (`spin-button`), winner dialog (`winner-dialog`), winner name display (`winner-name`), mute toggle, and "Remove & continue" functionality.
  - `src/lib/wheel.ts`: Core wheel calculation and parsing utilities.
  - `src/lib/sound.ts`: WebAudio tick and fanfare generator.
  - `src/lib/confetti.ts`: Lightweight canvas confetti utility.
  - `scripts/spinner-test.ts`: Playwright end-to-end test suite.

## Data model
- **Current Implementation**: Client-side state only; no backend database integration implemented yet.
- **Competitor Reference Model (Wheel of Names Teardown, [2026-08-13])**:
  - `entries`: Objects containing `{ text, image, color, weight, enabled }`.
  - `config`: Objects containing `{ spinTime, hubSize, colorSettings, sounds, autoRemoveWinner, displayWinnerDialog, winnerMessage, isAdvanced }`.

## Key logic
- **Entry Parsing (`parseEntries`)**: Parses input strings supporting weight syntax (e.g., `Name*3`) up to a maximum limit of 1000 entries.
- **Segment Building (`buildSegments`)**: Generates visual wheel segments proportional to entry weights.
- **Winner Selection (`pickWinner`)**: Performs a weighted random selection.
- **Rotation Target (`computeTargetRotation`)**: The winner is drawn *first*, and the wheel rotation is computed to land precisely on that winner. This guarantees that the visual animation always matches the mathematically fair draw. Uses `easeOutQuint` for smooth deceleration.
- **Audio Handling**: WebAudio API manages audio ticks and victory fanfares. `primeAudio()` must be invoked on the first user click to satisfy iOS audio policy requirements.

## Integrations & APIs
- **Current App**: No external APIs or backend integrations active; entirely client-side.
- **Competitor Reference (Wheel of Names, [2026-08-13])**: Built on Vue 3 + Quasar SPA hosted on Fastly, utilizing REST endpoints under `/api/v3/*` (wheels, wheels/shared, gallery, users, api-keys, client-settings, admin/*), Firebase backend (`wheel-of-names-firebase` with Google Auth + RTDB), and Freestar ads.

## Status
- **Milestone 1**: Completed [2026-08-13/14].
- **Deployments**: Preview and production environments are live [2026-08-14], gated by workspace members.
- **Current Directive**: Stan issued a strict "STOP" order on further development until he re-engages. Do not initiate Milestone 2 unprompted.

## Gotchas & lessons
- **iOS Audio Unlock**: Mobile browsers (especially iOS Safari) require an explicit user interaction (`primeAudio()`) on the first click to unlock WebAudio context.
- **Fair Draw Sequencing**: Calculating the winning entry *before* calculating the target rotation angle is critical to maintain absolute synchronization between the fair random draw and the visual stopping point.
- **Testing**: Playwright e2e test suite (`scripts/spinner-test.ts`) successfully verifies local builds.

## Open items
- **Platform Migration**: Execute migration from the old platform to Stan's own GitHub + Firebase Hosting + Convex account once authorized.
- **Roadmap Milestones (Pending Stan's Go-Ahead)**:
  - **M2**: Customization (colors/themes, per-entry images, sounds, spin duration, advanced mode).
  - **M3**: Accounts + save/share functionality.
  - **M4**: Gallery + moderation tools.
  - **M5**: Advanced upgrades (AI wheels, multiplayer, analytics, CSV/Sheets import, embed + API, brackets, video reveals).
- **Unresolved Decisions**: Final product name (SpinWheel is a temporary placeholder), monetization/ad strategy, and selection of M5 upgrade features.
