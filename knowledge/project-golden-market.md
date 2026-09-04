# Golden Market — options/earnings signals app
Golden Market is a quantitative options and earnings signals web application designed for Stan Braxton to identify, backtest, and trade ATM earnings straddles and non-earnings stock dip reversals. The platform combines automated data ingestion, custom risk algorithms, real-time tape and scalp tools, and broker execution links to manage directional and volatility strategies.

## Summary
Golden Market serves as Stan Braxton's proprietary quantitative market-signals application. It primarily tracks and trades ATM earnings straddles priced below historical moves, alongside a secondary dip-reversal board for non-earnings equities.

## Business & pricing
- **Pricing/Business Model:** Proprietary personal trading tool for Stan Braxton with no multi-tenant or public subscription tiers.

## Architecture
- **Tech Stack:** A React/Vite + Convex web app (currently hosted on the old platform; slated for migration to Stan's own GitHub + Firebase Hosting + Convex account).
- **Authentication:** Authenticated template default with `workspace_members` access gate.
- **Database & Seeding:** Convex DB with self-seeding (`backtestSeed.seed`) running in refresh/ensureFresh/cronRefresh routines. Manual `bunx convex run` targets DEV unless production seeding is explicitly routed.
- **Cron & Refresh:** Automated cron refresh runs every 30 minutes (skipping ET 22:00-07:00); client `ensureFresh` gates refresh to a 15-minute interval.

## Data model
- **Backtest Data:** Generated via `convex/backtestData.ts`; manual edits are prohibited.
- **Dip Bars:** `dipBars` table stores 130 trading days of universe `[open, close]` JSON per day-document (`convex/dip.ts`, `convex/massive.ts`).
- **Schemas:** Custom Convex tables and modules manage ORATS chains, gamma walls, earnings calendars, and scalp logs (`convex/orats.ts`, `convex/schwab.ts`).

## Key logic
- **Earnings Straddle Signal:** Value Ratio (`vr`) = `median(prior |earnings moves|) / straddle cost%`; flagged at `vr ≥ 1.15`. Universe: ~475 liquid names (20-day average option volume ≥5k, market cap ≥$2B, price ≥$10, minimum 4 prior events) [2026-08-29].
- **Straddle Backtest:** Sep 2025–Aug 2026 ORATS chain-level walk-forward (n=90): 43% win rate, avg +9.7%/trade mid-fills (+4.4% ask-in/bid-out), +27%/yr at 3% risk, 15% max drawdown, worst streak 11 losses [2026-08-29].
- **Position Sizing & Execution:** Sizing is 1% base +1% per 0.25 VR above threshold (capped at 3%). BMO entries occur on the prior business day; AMC/unknown entries occur on earnings day. Exits occur the next business day. Live straddle cost is re-checked at entry, and repriced-rich picks become `missed`. Typical week generates 0-4 plays.
- **Gamma-Wall / Pin-Risk Layer:** Computes GEX directly from ORATS strikes containing gamma and OI [2026-08-30]. Far-half gamma-wall trades showed +23.0%/trade vs -3.7% for near-half, improving risk profile. Constants: `PIN_WALL_PCT=3`, `PIN_SIZE_FACTOR=0.5`, `WALL_DTE_MAX=45`. `suggestedRisk(vr, pinRisk)` halves size and displays a red PIN RISK badge [2026-08-30].
- **Dip-Reversal Stock Board (`/dip`):** Buys non-earnings 5-day return ≤ −10% losers at next open, holding 5 days. Market gate requires equal-weight universe index to be above 100-day and 20-day SMA; size is halved when 20-day index volatility > 25% [2026-08-31]. Signals generate once per newest bar day; entries fill at next bar-day open, exits at close of entry+4 bar-days. Earnings exclusions use Finnhub calendar (deferred on calendar errors). Stock-only strategy (crashed-name options rejected due to inflated IV) [2026-08-31].
- **Scalp & Tape Tools (`/scalp`):** Discretionary cockpit and tracker containing levels, whale-flow tape, active contracts, watchlist, and trade tracker [2026-08-31]. Real-time option flow derived from Massive option snapshot deltas (real-time only while active); spot prices inferred via put-call parity.
- **Rejected Research & Methodology Rules:**
  - Massive Options Advanced flow layer showed no edge (2024-09..2026-08); recommended for cancellation. Core app does not depend on Massive options endpoints for earnings straddles.
  - Rejected strategy concepts: pre-earnings run-up straddles, PEAD at options layer, momentum calls, VRP-inverted straddles, IV-crush overshoot, directional tilt, intraday 0DTE grids, capacity expansion below current liquidity thresholds [2026-08-30].
  - Methodology law: Long-options backtests must be checked against structure-matched random-name controls; mid-to-conservative fill gaps run 6-14 points/trade.
  - Transcribed courses (13 Market Moves, Learn Grow Profit/Juicy Trades) fully tested and failed; do not revisit or build option sims on top of them [2026-08-30, 2026-08-31].
  - Volume-filtered intraday backtests must measure volume only up to entry time to prevent lookahead bias [2026-08-31].
  - News backtests: chasing positive headlines underperforms; negative news overlaps with dip-reversal edge without changing the board [2026-08-31].

## Integrations & APIs
- **Finnhub:** Used for `/calendar/earnings` as the confirmed upcoming earnings date/time source (ORATS `nextErn` is unreliable/`0000-00-00`). Used for earnings exclusion in dip-reversal board.
- **ORATS:** Bulk scan via `hist/cores?tradeDate=X&fields=...` (~6k tickers per request, walk back up to 6 business days for trade date; uses `pxCls`, `mktCap` in thousands, `ernDates` as `M/D/YYYY`). Entry/exit quotes via ORATS `live/strikes` (same day) or `hist/strikes` (late grading), with intrinsic fallback via `hist/dailies` `clsPx`. `ernStraPct` and `ernMv` arrays provide prior 12 earnings. Gamma and OI via `hist/strikes`/`live/strikes`.
- **Massive:** Powers dip-reversal data tables (`convex/massive.ts`), news data, and real-time option snapshot deltas for the scalp tape.
- **Schwab Trader API:** Approved and integrated (App ID: `d429c88d-0c0d-46d7-984f-89078ef8b34c`, products Accounts and Trading Production + Market Data Production, order limit 120, callback `https://127.0.0.1`). OAuth tokens: access token lasts 30 min; refresh token lasts 7 days (does not rotate, requiring weekly re-consent). Local token refresh managed via `schwab_refresh.py`, app seeding via `schwab_seed_app.py`. Powers `/tape` live tape page, 2-second polling, surge log/beep, and Schwab real-time candles for scalp levels with Massive fallback [2026-09-03].

## Status
- **Production Deployments:** Production and preview environments active on the legacy platform, slated for migration to Stan's GitHub + Firebase Hosting + Convex account [2026-08-29].
- **Earnings Straddle & Gamma-Wall:** Live with automated scoring and pin-risk mitigation [2026-08-30].
- **Dip-Reversal Board (`/dip`):** Shipped to production as board #2 [2026-08-31].
- **Scalp Cockpit & Schwab Tape (`/scalp`, `/tape`):** Shipped with live Schwab integration [2026-08-31, 2026-09-03].

## Gotchas & lessons
- **Database Seeding:** Production DB self-seeds via `backtestSeed.seed` in cron/refresh routines; manual `bunx convex run` targets DEV unless production seeding is explicitly routed.
- **Grading Integrity:** Never simplify exit grading to intrinsic-only; remaining time value the day after earnings is material to edge.
- **Generated Files:** `convex/backtestData.ts` is auto-generated; never hand-edit.
- **Data Providers:** Finnhub is required for upcoming earnings dates because ORATS `nextErn` returns `0000-00-00`. ORATS bulk scan requires `pxCls` (not `stkPx`), `mktCap` in thousands, and `ernDates` as `M/D/YYYY`. Walk back up to 6 business days if a trade date is missing. Walk-forward backtests must strictly use earnings events *before* the trade date (`ernStraPct`/`ernMv` arrays).
- **Asset Classes:** Crashed-name options have inflated IV and should be avoided (stock-only dip reversals).
- **Backtest Bias:** Volume-filtered intraday backtests must measure volume only up to entry time to prevent lookahead bias.
- **Schwab Auth & Proxy:** Schwab consent forms live inside `sws-gateway.schwab.com` iframes; stale proxy IPs can trigger Akamai 403 errors (restart browser with proxy). Auth codes expire quickly and must be driven in-browser. Weekly re-consent is required because Schwab refresh tokens do not rotate.
- **Testing & Tooling:** E2E testing (`scripts/golden-test.ts`) requires `bun run dev` running first from project root targeting `localhost:5173`. Playwright requires both versioned Chromium packages (`bunx playwright@1.62.1 install chromium chromium-headless-shell`) and plain `bunx playwright install` after sandbox resets. Biome linter forbids `??=` in expressions.

## Open items
- Migrate the application from the legacy platform to Stan's own GitHub + Firebase Hosting + Convex account.
- Cancel Massive Options Advanced before renewal due to lack of edge in options flow layer.
