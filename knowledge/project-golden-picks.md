# Golden Picks — MLB prediction app
Golden Picks is a public web application designed for baseball fans and bettors, providing recommended MLB team picks per game with win probabilities, confidence ratings, live market odds, and market edge analysis. Built for Stan Braxton, the app aggregates quantitative models, lineup data, and sharp odds to track performance and paper-trade strategic betting edges.

## Summary
The application runs automated refreshes every 30 minutes to grade pending games, sync live odds, evaluate daily picks, and update model ratings. It features multiple specialized views including the main win-probability model, a Value Board based on sharp devigged lines, Golden Underdogs, and the Golden Formula lineup model. Live records are isolated from retrodictive backfills via the `liveFrom` marker.

## Business & pricing
- **Access:** Publicly accessible (`auth.mode: public`) with no paywall or authentication requirements.
- **Staking & Tracking:** Standardizes flat 1-unit flat betting across tracked categories (Golden Underdogs, Value Board, Golden Formula, and Daily Picks).
- **Paper Trading:** Golden Underdogs and Golden Formula operate in paper-trading mode to evaluate theoretical edges without claiming guaranteed live profitability.

## Architecture
- **Stack:** Built as a React/Vite + Convex web app (currently hosted on the old platform; slated for migration to Stan's own GitHub + Firebase Hosting + Convex account).
- **Backend Modules (`convex/`):**
  - `model.ts`: Core math (Pythagorean, Log5, HFA, confidence, factors).
  - `odds.ts`: Odds API integration.
  - `pitchers.ts`: Probable starter lines and FIP scoring.
  - `underdogs.ts`: Golden Underdogs EV calculations.
  - `teamstats.ts`: Reconstructed wRC+, xFIP, and error estimates from ESPN raw data.
  - `mlb.ts`: Sync, refresh actions, and core queries (`gamesForDate`, `record`, `teamRatings`).
  - `lineup.ts`: Lineup completeness and formula evaluation.
  - `daily.ts`: Daily picks evaluation.
  - `crons.ts`: Scheduled 30-minute refresh jobs (`mlb:cronRefresh`).
  - `elo.ts`: Elo rating engine and bootstrapping.
- **Frontend Pages & Components (`src/`):** `PicksPage`, `RecordPage`, `RatingsPage`, `ValuePage`, `FormulaPage`, and `PickCard.tsx`.

## Data model
- **`games`:** Schedule, scores, status, series fade flags (`games.seriesFade`), retro creation markers (`games.retro`), and lineup application checks (`games.lineupApplied`).
- **`teams`:** Stores team Elo ratings (`teams.elo`).
- **`pitchers`:** Probable starter stats cached for 12 hours.
- **`teamAdvanced`:** Reconstructed advanced team metrics (wRC+, xFIP, error rates) cached for 12 hours.
- **`underdogBets` / `valueBets` / `formulaBets`:** Bet logs storing locked odds at game schedule, closing odds at first pitch, CLV, unit stakes, and W-L results.
- **`dailyPicks`:** Tracks daily "Pick of the Day" and "Value of the Day" selections.

## Key logic
- **Main Model:** Combines 65% Pythagorean expectation (exponent 1.83) and 35% win percentage (regressed toward .500 under 60 games), plus a last-10 form nudge. Strengths combine via Log5, adjusted for home-field advantage (+0.155 log-odds ≈ 54%) and starter FIP edge (league average 4.10, regressed by IP, capped at ±0.35 logit). Outputs are clamped to 28%–72% [2026-08-24].
- **Elo Engine:** Operates with K=5, home advantage = 15, margin-of-victory scaling (`convex/elo.ts`). Seeded via walk-through history [2026-08-24].
- **Series Fade:** Drops confidence by 20 points if the pick side already lost to the same opponent in the series, marked with an amber badge.
- **Golden Underdogs:** Implements Stan's specific formula: `ProjRuns_A = 4.4 × (wRC_A/100) × (xFIP_B/4.3) + 0.15 × errors_B`, Pythagenport exponent `(totalRuns/2)^0.287`, EV = `p × payout − (1 − p)`, quarter-Kelly staking floored at 0, flagged at EV ≥ 3% [2026-08-12].
- **Value Board (`/value`):** Uses devigged sharp lines (Pinnacle prioritized), calculates EV against best book prices, flags at EV ≥ 1% with quarter-Kelly sizing, and triggers outlier badges for quotes > 10% [2026-08-24]. Note: Exchange commission (Matchbook/Betfair) is unmodeled, overstating EV by 1–2%.
- **Golden Formula (`/formula`):** Lineup completeness model (`LINEUP_COEF=0.887` × lcDiff in logit space). Requires 9 posted players and 9 known regulars (≥3 starts) [2026-08-24]. Fading the formula underperforms (−6.4% ROI vs +1.3%) [2026-08-30].
- **Daily Picks:** Selects top-confidence main model pick ("Pick of the Day") and highest-EV non-outlier Value Board play ("Value of the Day"), locking ~2 hours pregame [2026-08-29].
- **CLV Tracking:** Captures locked odds when games are scheduled; closing odds are captured at first pitch. Closing line value (`clv`) equals payout at locked odds minus payout at closing odds per dollar.

## Integrations & APIs
- **The Odds API:** Fetches live odds across multiple markets using `regions=us,eu` (required to capture Pinnacle in the EU region).
- **MLB StatsAPI:** Fetches pregame lineups (`schedule?sportId=1&date=...&hydrate=lineups,team`). Requires the `team` hydrate for abbreviation parsing; handles name mappings (AZ/ARI, CWS/CHW) and fails gracefully without halting refreshes.
- **ESPN API:** Retrieves team stats and schedules using a resilient fallback URL chain (`site.web.api.espn.com` → `site.api.espn.com` → `cdn.espn.com`) to bypass server-side 403 blocks.

## Status
- **Launch:** Initialized on [2026-08-12].
- **Model Performance Audits:** Logloss stands around ~0.689 out-of-sample [2026-08-15].
- **Recent Records (Clean Prod):** Golden Underdogs: 57-82 (−16.8u); Value Board: 40-58 (−16.2u) as of [2026-08-30].
- **Features Shipped:** Cron refresh every 30 min (skipping 3–8 AM ET), Value Board [2026-08-24], Golden Formula lineup model [2026-08-24], Daily Picks [2026-08-29], and stale-quote guards [2026-08-30].

## Gotchas & lessons
- **Convex CLI Deployment Pitfall:** Running `bunx convex data ... --prod` can silently hit development environments if `.env.local` contains a dev CONVEX_DEPLOY_KEY. Always rely on programmatic queries for production data verification.
- **Codegen Requirement:** After modifying Convex modules, run `CONVEX_TMPDIR=./tmp bunx convex codegen` (ensuring the target temp directory exists) to prevent stale `internal.*` type errors.
- **Stale-Quote Guard:** Sportsbooks occasionally emit anomalous garbage odds (+1532/+2900 on coin flips). A stale-quote guard (`isStalePrice`) rejects quotes paying >3× fair payout and enforces EV caps. Historical phantom records were purged via `purgeStaleQuotes` [2026-08-30].
- **Backtest vs. Live Separation:** Past-date backfills use current season ratings, making them retrodictive. They are explicitly labelled `backtest` and separated from live records via the `liveFrom` metadata key. Do not present backtest numbers as live alpha.
- **Exchange Commission:** Betting exchanges (Matchbook/Betfair) do not model commission, leading to a 1–2% overstatement of EV in those markets.

## Open items
- Complete migration from the old hosting platform to Stan's own GitHub + Firebase Hosting + Convex account.
- Expand price archives for glamour fade analysis.
- Evaluate getaway day factors as a potential predictive signal.
