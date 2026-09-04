# WaffleHouse — seat-sweepstakes marketplace

WaffleHouse is Stan's multi-tenant web marketplace with gamified, seat-based sweepstakes, modeled after "Platform Sixx" (no real reference existed; built from spec). It enables transparent prize draws using weighted seats, integrated digital wallets, multi-tiered memberships, and real-time chat. Milestone 1 shipped 2026-08-13.

## Deployment
- Project name: `wafflehouse`. Originally built and hosted on the legacy hosting platform (Stan's previous engineering assistant's app-building environment); intended migration target is Stan's own GitHub + Firebase Hosting + Convex account.
- Preview deploy went live 2026-08-13.
- Convex dev deployment instance: `quaint-chihuahua-275`; production instance: `limitless-antelope-933`.
- Access controlled via `workspace_members`; auth mode is authenticated.
- First production deploy happened 2026-08-14 with Stan's explicit OK ("build them all out to live", from the app discussion thread); gated by `workspace_members`.
- **Stan revoked deploy autonomy for this app (2026-08-13 20:31): do NOT deploy or update the build (even preview) without his explicit permission.** Note: `bun run sync:build` pushes Convex functions to the dev deployment that backs the preview — additive changes are OK for local testing but must stay backward-compatible with the deployed frontend.

## Data model (convex/schema.ts)
- `vendors`: `name`, `slug`, `inviteCode` (format `WFH-XXXXXX`), optional `adminInviteCode` (format `WFA-XXXXXX`) — redeeming the admin code joins as **admin** or upgrades an existing member; generate/rotate it on the Admin page.
- `memberships`: role is one of owner/admin/member.
- `wallets`: one per user+vendor pair — `cash` in cents (Universal Cash), `credits` (Store Credits ₢). Signup bonus: 2500¢ + ₢500.
- `transactions`: ledger of wallet activity.
- `events`: status draft/published/paused/drawn; seed is fixed at publish time as `${eventId}:${publishedAt}`; stores `winnerSeatId`/`winnerUserId`/`winnerRoll`/`winnerTotalWeight`. Also has `prizeDescription` (optional text) and `prizeImageIds` (up to 8 `_storage` ids). The `generatePrizeUploadUrl` mutation is admin-scoped; `updateEvent` garbage-collects removed storage files [2026-08-13].
- `seats`: status open/reserved/taken, weight 1–10, `reservedUntil`; reservations expire after 90 seconds via a computed `effectiveStatus`.
- `messages`: chat, indexed by `by_room` [vendorId, eventId] — undefined `eventId` means the workspace-wide room, a set value means a per-event discussion; has a `pinned` flag; deletion allowed for admin-or-author, pinning admin-only [2026-08-13].

## Key mechanics
- **Deterministic draw** (`convex/lib/draw.ts`): FNV-1a hash of the seed feeds a mulberry32 PRNG, which does a cumulative-weight pick over seats sorted by `seatNumber`. Roll and total weight are exposed for audit purposes (shown in the winner modal).
- **Tenancy** (`convex/lib/tenancy.ts`): `requireMembership`/`requireAdmin` guard every vendor-scoped function. Invite-code gate: users with no membership see a JoinGate to redeem a code or create a workspace.
- **`updateEvent`** (2026-08-13): draft events are fully editable (changing seat count rebuilds the seat grid; boost cost follows entry cost if they matched); published/paused events allow editing details only (title/description/prize) — seats and pricing are locked for fairness; drawn events are not editable at all (Edit button hidden). Covered by E2E test `scripts/test-edit-event.ts`.
- Entry costs are charged in credits; a boost costs the entry cost again and adds +1 to seat weight. Cash→credits conversion is 1¢=₢1 (an M1 development convenience).
- Constants live in `convex/events.ts`: `RESERVATION_MS=90_000`, seat counts allowed 2–200, max weight multiplier ×10.

## Frontend
- Forced dark theme (`ThemeProvider defaultTheme="dark"`), warm amber "midnight griddle" palette defined in the `.dark` block of `index.css`.
- `VendorContext` persists the active vendor in `localStorage` under `wafflehouse.vendorId`. `WaffleLayout` provides the top nav: Events / Store / Affiliate / Admin (admin-only), plus a `WalletChip` (test IDs `wallet-cash`/`wallet-credits`).
- Pages: `EventsPage` (shows prize photo banner on cards), `EventDetailPage` (seat grid with test IDs `seat-N` + `data-status`, `confirm-seat`, `winner-name`, `PrizeGallery` + `prize-description`, live discussion via `ChatRoom`), `ChatPage` (the `/chat` tab), `StorePage`, `AffiliatePage`, `AdminEventsPage` (`new-event` control, dialog field IDs `ev-title`/`ev-desc`/`ev-prize`/`ev-prize-desc`/`ev-seats`/`ev-cost`, `AdminInviteCard` with test IDs `admin-invite-code`/`generate-*`/`rotate-admin-invite`).
- `components/ChatRoom.tsx` is a reusable chat component (test IDs `chat-input`/`chat-send`/`msg-*`/`pin-*`/`del-*`/`pinned-*`). `components/PrizeImagesInput.tsx` handles multi-image upload to Convex storage (test IDs `prize-file-input`/`prize-thumb-N`).
- Confetti effect implemented as a dependency-free canvas component (`components/Confetti.tsx`).

## Roadmap / scope agreed with Stan
- M1 (done): core engine, admin console, tenancy, wallet.
- M2: Store product marketplace (planned).
- M3: Affiliate commissions (planned).
- This roadmap has been communicated to Stan; the current Store and Affiliate tabs are functional placeholders that already incorporate wallet and invite mechanics.

## Testing
- E2E test scripts (in `scripts/`): `test-wafflehouse-m1.ts` (full flow), `test-edit-event.ts`, `test-prize-chat.ts` (gallery + chat + moderation), `test-admin-invite.ts`. Demo seeding + screenshots: `scripts/shots.ts` → outputs to `shots/`.
- To run tests against a manual preview: start `bunx vite preview --port 4173`, then run `APP_URL=http://localhost:4173 bun scripts/<test>.ts`.
- Gotcha: the `runTest().catch()` wrapper in these scripts swallows errors — add explicit error printing in the catch block when debugging.
- Gotcha: run `CONVEX_TMPDIR=./tmp bun run sync:build` (otherwise you'll hit an `EPERM` error).
- Gotcha: Playwright needs `bunx playwright install chromium chromium-headless-shell` run locally first.
- Gotcha: never run `pkill -f "vite preview"` combined with other work in the same command — it will kill your own shell along with the preview process.

## Open items
- Implement Milestone 2 (Store product marketplace).
- Implement Milestone 3 (Affiliate commissions).
- Migration of hosting from the legacy platform to Stan's own GitHub + Firebase Hosting + Convex account is planned but not yet done.
