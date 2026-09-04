# Golden Market — Market Signals App

Golden Market is Stan Braxton's quantitative options and earnings signals web application. As you assist Stan, you will manage a platform that combines automated data ingestion, custom risk algorithms, real-time tape and scalp tools, and broker execution links to identify, backtest, and trade ATM earnings straddles and non-earnings stock dip reversals.

## Architecture & Hosting

- **Tech Stack:** React/Vite + Convex web app.
- **Hosting:** Currently hosted on the legacy hosting platform (prod and preview environments active), slated for migration to Stan's own GitHub + Firebase Hosting + Convex account [2026-08-29]. Research and credentials reside in `projects/golden-market/`.
- **Authentication & Access:** Authenticated template default with `workspace_members` access gate.
- **Database & Seeding:** Convex DB with self-seeding (`backtestSeed.seed`) running inside refresh, `ensureFresh`, and scheduled job routines. Manual `bunx convex run` targets DEV unless production seeding is explicitly done through app-specific paths.
- **Scheduled Jobs & Refresh:** Scheduled job refresh runs every 30 minutes, skipping ET 22:00-07:00; client `ensureFresh` gates refresh to a 15-minute interval.

## Earnings Straddle Model

- **Signal Logic:** Value Ratio (`vr`) = `median(prior |earnings moves|) / straddle cost%`; flag at `vr ≥ 1.15`. Universe: ~475 liquid names (20-day average option volume ≥5k, market cap ≥$2B, price ≥$10), with at least 4 prior events [research, 2026-08-29].
- **Backtest Performance:** Sep 2025–Aug 2026 ORATS chain-level walk-forward, n=90, win rate 43%, average +9.7%/trade mid-fills (+4.4% ask-in/bid-out), +27%/yr at 3% risk, max drawdown 15%, worst streak 11 losses [research/chain_backtest.py, 2026-08-29].
- **Position Sizing:** 1% base +1% per 0.25 VR above threshold, capped at 3%.
- **Entry & Exit Execution:** BMO entries occur on the prior business day; AMC/unknown entries occur on earnings day. Exits occur the next business day. Entry re-checks live straddle cost; repriced-rich picks become `missed`. A typical week flags 0-4 plays; an empty board is normal.
- **Backtest Data Generation:** Backtest page data comes from generated `convex/backtestData.ts`; never hand-edit this file.

## Data Providers & Ingestion Gotchas

- **Finnhub:** `/calendar/earnings` is the only confirmed upcoming date/time source; ORATS `nextErn` is always `0000-00-00`. Finnhub is also used for earnings exclusion in the dip-reversal board (if calendar errors, signals are deferred, not skipped).
- **ORATS Bulk Scan:** `hist/cores?tradeDate=X&fields=...` returns all ~6k tickers in one request. Walk back up to 6 business days for a trade date if needed. Stock price field is `pxCls` (not `stkPx`); `mktCap` is in thousands; core `ernDates` are formatted as `M/D/YYYY`.
- **Quotes & Grading:** Entry/exit quotes use ORATS `live/strikes` for same-day data or `hist/strikes` for late grading, with intrinsic fallback via `hist/dailies` `clsPx`. `ernStraPct` and `ernMv` arrays represent the last 12 earnings; walk-forward tests must strictly use events prior to the trade date.
- **Grading Integrity:** Never simplify exit grading to intrinsic-only: remaining time value the day after earnings is material to the edge.

## Gamma-Wall / Pin-Risk Layer

- ORATS `hist/strikes`/`live/strikes` include gamma and OI, allowing GEX to be computed directly [research, 2026-08-30].
- Dealer gamma tilt showed no signal. However, gamma-wall distance significantly improved risk: in VR≥1.15 trades, far-half trades returned +23.0%/trade vs near-half at −3.7%, reducing maximum drawdown and worst streak while maintaining similar total return [research/gex_backtest.py, 2026-08-30].
- Shipped implementation: `convex/orats.ts gammaWall()`, constants `PIN_WALL_PCT=3`, `PIN_SIZE_FACTOR=0.5`, `WALL_DTE_MAX=45`; `suggestedRisk(vr, pinRisk)` halves size and the UI displays a red PIN RISK badge [deployed, 2026-08-30].

## Dip-Reversal Stock Board (`/dip`)

- Shipped to production as board #2. Strategy: buy non-earnings 5-day return ≤ −10% losers at next open, hold for 5 days; market gate requires the equal-weight universe index to be above its 100-day and 20-day SMA; halve size when 20-day index volatility > 25% [research/reversal, 2026-08-31].
- Implementation details: `convex/dip.ts`, `convex/massive.ts`, `dipBars` table storing 130 trading days of universe `[open, close]` JSON per day-doc; self-backfills via `dip.backfill` and seeds production via scheduled jobs or the first `/dip` visit.
- Signal Generation: Signals generate once per newest bar day; entries fill at the next bar-day open, and exits occur at the close of entry+4 bar-days.
- Asset Class Constraint: Stock-only strategy; crashed-name options were IV-inflated and rejected [research/reversal/test_dip_calls.py, 2026-08-31].

## News, Tape, and Scalp Tools

- **News:** Massive news backtests showed that chasing positive headlines underperforms; negative news overlaps with the dip-reversal edge and did not alter the board [research/news, 2026-08-31].
- **Scalp Cockpit (`/scalp`):** A discretionary cockpit and tracker (not an auto-signal board) featuring levels, whale-flow tape, active contracts, watchlist, and trade tracker [shipped, 2026-08-31].
- **Scalp Flow:** Scalp flow derived from Massive option snapshot deltas is not a full print tape; it operates in real-time only while Options Advanced access remains active. Underlying real-time spot is inferred via put-call parity.

## Schwab Integration

- **App Configuration:** Schwab Trader API app is approved and ready: App ID `d429c88d-0c0d-46d7-984f-89078ef8b34c`, products Accounts and Trading Production + Market Data Production, order limit 120, callback `https://127.0.0.1`. Client credentials and tokens live in project files, not in this document [Schwab portal, 2026-09-03].
- **OAuth & Token Lifecycle:** OAuth consent completed; both accounts linked. Access token lasts 30 minutes; refresh token lasts 7 days and does not rotate on refresh, requiring weekly re-consent [Schwab, 2026-09-03].
- **Management Scripts:** Refresh local tokens using `uv run python golden_market/scripts/schwab_refresh.py`; seed the app with `schwab_seed_app.py prod` and `dev` after consent/refresh.
- **Shipped Features:** `convex/schwab.ts`, `/tape` live tape page, client 2-second polling, surge log/beep, and Schwab real-time candles for scalp levels with Massive fallback [deploy, 2026-09-03].
- **Consent & Browser Gotchas:** Schwab consent forms run inside `sws-gateway.schwab.com` iframes; stale proxy IPs can trigger Akamai 403 errors, so restart the browser with a proxy. Auth codes in redirect URLs expire quickly and must be driven in-browser rather than pasted manually.

## Rejected Options Research & Methodology Rules

- **Options-Flow Layer:** Massive Options Advanced showed no edge in the 2024-09 through 2026-08 backtest; cancellation before renewal is recommended. Core app functions do not depend on Massive options endpoints for earnings straddles [research/flow, 2026-08-30].
- **Rejected Strategy Concepts:** Pre-earnings run-up straddles, PEAD at the options layer, momentum calls after matched controls, VRP-inverted straddles, IV-crush overshoot, directional tilt, intraday 0DTE grids, and capacity expansion below current liquidity thresholds [research/strategy2/PLAN.md, 2026-08-30].
- **Methodology Law:** Any long-options backtest that looks favorable in a bull period must be checked against a structure-matched random-name control before belief. Mid-to-conservative fill gaps typically run 6-14 points/trade.
- **Transcribed Courses:** 13 Market Moves and Learn Grow Profit/Juicy Trades courses were fully transcribed and backtested; both failed. Do not revisit or build option simulations on top of them [research/mm13, 2026-08-30; research/lgp, 2026-08-31].
- **Volume Filters:** Volume-filtered intraday backtests must measure volume only up to entry time; full-day relative volume creates lookahead edges that vanish in live trading [research/lgp, 2026-08-31].

## Testing, Development, and Gotchas

- **End-to-End Testing:** When running `scripts/golden-test.ts`, start `bun run dev` first from the project root directory; tests target `localhost:5173` by default.
- **Playwright Setup:** Install both Playwright Chromium packages after test environment resets: versioned `bunx playwright@1.62.1 install chromium chromium-headless-shell` and plain `bunx playwright install ...`.
- **Dependencies:** If `node_modules` symlink targets are pruned, recreate them and run `bun install`.
- **Linter Rule:** Biome linter strictly forbids `??=` in expressions.

## Open Items

- Migrate the application from the legacy hosting platform to Stan's own GitHub + Firebase Hosting + Convex account.
- Cancel Massive Options Advanced before renewal due to lack of verified edge in the options flow layer.
