# WaffleHouse — seat-sweepstakes marketplace

WaffleHouse is a multi-tenant web marketplace for gamified, seat-based sweepstakes built for vendors and their communities. It enables transparent prize draws using weighted seats, integrated digital wallets, multi-tiered memberships, and real-time chat.

## Summary
The platform facilitates vendor-managed sweepstakes where users purchase or reserve weighted seats to win prizes through a verifiable deterministic draw engine. Milestone 1 established core engine functionality, tenancy controls, wallet ledgers, and admin consoles [2026-08-13].

## Business & pricing
- **Vendors & Memberships:** Tenants have a name, slug, user invite code (`WFH-XXXXXX`), and an optional admin invite code (`WFA-XXXXXX`) which joins or upgrades a member to an administrator role (generated and rotated via the Admin page). Memberships map to roles: owner, admin, and member. Unaffiliated users encounter a JoinGate to redeem invite codes or create workspaces.
- **Wallets:** Tracked per user and vendor combination, containing Universal Cash (measured in cents) and Store Credits (₢). The standard signup bonus provides 2500¢ + ₢500.
- **Pricing & Economy:** Entry costs are charged in credits. Boosts cost an additional entry fee and add +1 to the seat's weight. Cash-to-credits conversion is set at 1¢ = ₢1 as an M1 development convenience.
- **Roadmap scope:** Milestone 1 core features are complete. Milestone 2 (Store product marketplace) and Milestone 3 (Affiliate commissions) are planned; current Store and Affiliate tabs serve as functional placeholders incorporating wallet and invite mechanics.

## Architecture
- **Stack:** A React/Vite + Convex web app (currently hosted on the old platform; slated for migration to Stan's own GitHub + Firebase Hosting + Convex account).
- **Environment & Hosting:** Convex dev deployment uses instance `quaint-chihuahua-275`; production uses `limitless-antelope-933`. Access is controlled via `workspace_members` with authenticated auth mode.
- **UI & Theme:** Forced dark theme via `ThemeProvider defaultTheme="dark"`, utilizing a warm amber "midnight griddle" palette in the `index.css` `.dark` block. Includes a dependency-free canvas confetti component (`components/Confetti.tsx`).
- **Routing & State:** `VendorContext` reads from `localStorage` (`wafflehouse.vendorId`). `WaffleLayout` provides top navigation for Events, Store, Affiliate, and Admin (restricted to admins), alongside a `WalletChip` with test IDs `wallet-cash` and `wallet-credits`.

## Data model
- `vendors`: fields include `name`, `slug`, `inviteCode`, and optional `adminInviteCode`.
- `memberships`: maps user-vendor relations to roles (`owner`, `admin`, `member`).
- `wallets`: stores `cash` in cents and `credits` (₢).
- `transactions`: financial ledger tracking ledger entries.
- `events`: supports status values (`draft`, `published`, `paused`, `drawn`). The seed is fixed at publish as `${eventId}:${publishedAt}`. Stores winner metadata (`winnerSeatId`, `winnerUserId`, `winnerRoll`, `winnerTotalWeight`), optional `prizeDescription`, and up to 8 `_storage` IDs in `prizeImageIds`. The `generatePrizeUploadUrl` mutation is admin-scoped, and event updates garbage-collect removed storage files [2026-08-13].
- `seats`: status values (`open`, `reserved`, `taken`), weight (1–10), and `reservedUntil`. Reservations expire after 90 seconds via computed effective status. Constants in `convex/events.ts` define `RESERVATION_MS = 90_000`, seat counts between 2–200, and max weight multipliers of ×10.
- `messages`: real-time chat indexed by `by_room` (`[vendorId, eventId]`). An undefined `eventId` represents the workspace room, while a set value indicates per-event discussion. Includes pinned flags; deletion is restricted to admins or authors, and pinning is admin-only [2026-08-13].

## Key logic
- **Deterministic Draw (`convex/lib/draw.ts`):** Uses an FNV-1a hash of the event seed feeding a mulberry32 PRNG to perform a cumulative-weight pick over seats sorted by `seatNumber`. The roll and total weight are exposed in the winner modal for complete auditability.
- **Tenancy (`convex/lib/tenancy.ts`):** Enforces `requireMembership` and `requireAdmin` checks on every vendor-scoped backend function.
- **Event Updates (`updateEvent`, [2026-08-13]):** Draft events are fully editable (changing seat counts rebuilds the grid; boost costs sync with entry costs if matched). Published or paused events allow details-only edits (title, description, prize) while locking seats and pricing for fairness. Drawn events are locked and non-editable (Edit button hidden). Covered by E2E tests in `scripts/test-edit-event.ts`.

## Integrations & APIs
- **Convex Storage:** Handles multi-image prize uploads via `components/PrizeImagesInput.tsx` (`prize-file-input`, `prize-thumb-N`) using admin-scoped storage URLs.

## Status
- Milestone 1 shipped [2026-08-13].
- First production deployment executed on [2026-08-14] with Stan's explicit authorization (`workspace_members` gate).
- **Deploy Restriction:** Stan revoked deploy autonomy for this app on [2026-08-13 20:31]. Do *not* deploy or update the build (even previews) without explicit permission. `bun run sync:build` can be used to push Convex functions to the dev deployment backing the preview (additive schema changes are acceptable for local testing if kept backward-compatible with the deployed frontend).

## Gotchas & lessons
- Set `CONVEX_TMPDIR=./tmp` when running `bun run sync:build` to prevent `EPERM` errors.
- Playwright requires local browser installation (`bunx playwright install chromium chromium-headless-shell`).
- Never run `pkill -f "vite preview"` in the same compound shell command as parallel work, as it terminates its own execution shell.
- Test error handling: The `runTest().catch()` wrapper in E2E scripts swallows errors—always include explicit error printing inside catch blocks.
- Testing scripts are located in `scripts/` (`test-wafflehouse-m1.ts`, `test-edit-event.ts`, `test-prize-chat.ts`, `test-admin-invite.ts`, and `shots.ts` outputting screenshots to `shots/`). Manual preview testing can be run via `bunx vite preview --port 4173` followed by `APP_URL=http://localhost:4173 bun scripts/<test>.ts`.

## Open items
- Implement Milestone 2 (Store product marketplace).
- Implement Milestone 3 (Affiliate commissions).
