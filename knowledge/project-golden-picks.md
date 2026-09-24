# Golden Picks — MLB predictions

Golden Picks is a public web application designed for baseball fans and bettors, providing recommended MLB team picks per game with win probabilities, confidence ratings, live market odds, and market edge analysis. Built for Stan Braxton, the app aggregates quantitative models, lineup data, and sharp odds to track performance and paper-trade strategic betting edges.

## Overview & Architecture

Golden Picks runs automated refreshes every 30 minutes (via scheduled job `mlb:cronRefresh`, skipping 3–8 AM ET) to grade pending games, sync live odds, evaluate daily picks, and update model ratings.

- **Project Name:** `golden-picks`
- **Access:** Public (`auth.mode: public`), built 2026-08-12.
- **Backend Modules (`convex/`):**
  - `model.ts`: Core model math (Pythagorean, Log5, HFA, confidence, factors). The main model is a tempered 50/50 current+Elo ensemble (`ensembleHomeProb`, T=0.7, clamp 28–72).
  - `odds.ts`: Odds API integration via tool gateway.
  - `pitchers.ts`: Probable starter lines and FIP scoring (cached 12h in `pitchers` table).
  - `underdogs.ts`: Golden Underdogs +EV math (Stan's formulas, verbatim).
  - `teamstats.ts`: Reconstructed wRC+, xFIP, and error-per-game estimates from ESPN raw data (cached 12h in `teamAdvanced`).
  - `mlb.ts`: Sync, refresh actions, and core queries (`gamesForDate`, `record`, `teamRatings`).
  - `lineup.ts`: Lineup completeness, formula evaluation, and bootstrap seeding (`lineupStarts` from `convex/lineupSeedData.ts`, meta `lineupSeeded`).
  - `daily.ts`: Daily picks evaluation (`dailyPicks` table).
  - `crons.ts`: Scheduled 30-minute refresh jobs.
  - `elo.ts`: Elo rating engine (K=5, ha=15, MOV) and bootstrapping (`elo:bootstrap` seeded from `convex/eloSeedData.ts`, walk-through through 8/23, excluding All-Star game, marking `RETRO_DATES` 8/16–18 and 8/20–23).
- **Frontend Pages & Components (`src/pages/`, `src/components/`):** `PicksPage`, `RecordPage`, `RatingsPage`, `ValuePage`, `FormulaPage`, and `PickCard.tsx`.

## Core Models & Formulas

### Main Win Probability Model
- **Strength:** 65% Pythagorean expectation (exponent 1.83) + 35% win percentage (regressed toward .500 below 60 games played), plus a small last-10 form nudge.
- **Log5 & HFA:** Two strengths combine via Log5, adjusted for home-field advantage (+0.155 log-odds ≈ 54%) and probable-starter edge in log-odds space.
- **Starter Quality:** FIP regressed toward league average (4.10) by innings (50 IP = half weight); net edge capped at ±0.35 logit (~±8 points).
- **Clamping & Edge:** Output clamped to 28%–72% because single MLB games are high variance. Edge = model pick prob − vig-free market prob (per-book vig removal, averaged).

### Golden Underdogs (Second Independent Model)
- Located at `/underdogs`. Implements Stan's specific formulas verbatim [stan, 2026-08-12]:
  - `ProjRuns_A = 4.4 × (wRC_A/100) × (xFIP_B/4.3) + 0.15 × errors_B`
  - Pythagenport exponent `(totalRuns/2)^0.287`
  - EV = `p × payout − (1 − p)`
  - Quarter-Kelly staking floored at 0
  - Flagged at EV ≥ 3% ⇒ "GOLDEN UNDERDOG PLAY".
- Cards show implied win%, Golden% (implied+EV), and R/R (payout ÷ P(lose)). Playable sides sort by EV then R/R.
- Sharp line = first of pinnacle, lowvig, betonlineag, matchbook (`regions=us,eu` required to capture Pinnacle in the EU region).
- *Research & Re-audit:* Systematically overconfident (avg pCal 51.4% vs market 46.1% vs actual 44.4%, n=18, -0.84u). Real bet re-audit (n=53: 21-32, -7.43u, -14% ROI) showed no variant profitable; GU edges are anti-signal. **Rule for you:** Never present GU plays as +EV. Always present them as Stan's formula output, not validated alpha.

### Value Board (`/value`)
- Third slate view (`convex/value.ts`, `valueBets`, `mlb:valueRecord`).
- No forecasting: pTrue = devigged sharp line (Pinnacle first), EV = pTrue × payout(best book price) − (1−pTrue), flagged at EV ≥ 1%, quarter-Kelly sizing, outlier badge for quotes > 10% (stale quote).
- *Caveat:* Exchange (Matchbook/Betfair) commission is NOT modeled, overstating EV there by ~1–2%.

### Golden Formula (Lineup Paper Model)
- Shipped to production 2026-08-24. Fourth slate view `/formula` (amber Paper-tracking tab).
- `pHome` = devigged sharp prob + clamp(±4 pts) of `LINEUP_COEF=0.887` × lcDiff in logit space.
- Sides at EV ≥ 1% (best book price) → "FORMULA PLAY", logged in `formulaBets` (mirrors `valueBets`), `mlb:formulaRecord`.
- *Lineup Data:* Pregame lineups fetched from MLB StatsAPI inside `refresh` (`schedule?sportId=1&date=...&hydrate=lineups,team` — team hydrate required for abbreviation; posted ~1–4h pregame; MLB→ESPN abbreviations differ only AZ→ARI, CWS→CHW). Try/catch protected—fetch failure never halts refresh. Doubleheaders matched by nearest gameDate.
- Lineups are facts not picks: backfilled onto locked games; starts folded into `lineupStarts` once per final (`games.lineupApplied`). Completeness requires 9 posted players AND 9 known regulars (≥3 starts) or no formula is shown.
- *Caveat:* Fading the formula loses more than it wins (fade 36-43, −6.4% ROI vs +1.3%); the opposite side is by construction the overpriced side [2026-08-30].
- *2-Week Backtest:* Over prod price archive (68 priced finals across 5 slates, 8/12–15, 8/19): EV ≥ 1% went 29-21, +6.34u (+12.7% ROI); EV ≥ 3% went 24-18, +6.42u (+15.3%). Lineup-lift-only plays went 26-16 +8.16u.

### Daily Picks (Shipped 2026-08-29)
- One tracked "Pick of the Day" (main model's top-confidence side) and "Value of the Day" (highest-EV non-outlier Value Board play) per day.
- `convex/daily.ts`: `evaluateDaily` runs from `refresh` after `upsertGames`; selection is provisional, locks ~2h before game's first pitch, then close/CLV/grade like bet logs. VOTD skips EV > `VALUE_EV_OUTLIER` (stale quotes).
- `dailyPicks` table; one-time bootstrap (`dailySeededV2`) seeds history from `liveFrom` onward at archived best prices (rows marked `retro`/seeded and excluded from live units). Queries: `daily:dailyRecord`, `daily:dailyForDate`. UI: banners on PicksPage, two `DailySection`s on `/record`.

## Bet Logs & CLV Tracking

- `underdogBets` / `valueBets` / `formulaBets`: one row per flagged side, opened only while game is `scheduled` at `lockedOdds`. Pre-start refreshes overwrite `latestOdds`, first-pitch value becomes `closingOdds`.
- `clv` = payout(locked) − payout(close) per $1.
- Grading runs from the game row alone. All three show on `/record`: W-L, flat 1u units, ROI, and CLV.
- *Initial CLV Issue:* Early audits showed closingOdds = latestOdds ?? lockedOdds, resulting in 0 CLV because pages weren't loaded pre-start. Scheduled refreshes near game starts resolved this.

## Series Fade & Factor Backtest

- **Series Fade (8/13):** `seriesFadeFor` drops confidence by 20 when pick-side already lost to this opponent in the last 5 days (`FADE_LOOKBACK_DAYS = 5`). Stores `games.seriesFade` + amber badge.
- **Series Fade record since inception:** there is no dedicated record query in the app for this — it has to be computed by pulling `mlb:gamesForDate` prod for every date since 2026-08-13 and filtering rows where `seriesFade` is set. As of 2026-09-24: live (non-retro) graded fade-flagged picks went 83-56 (59.7%); all live graded picks in the same window were 262-200 (56.7%) — so fade-flagged picks have run *slightly above* overall accuracy, not below; the badge is a caution flag, not a signal to bet against the pick. Monthly: Aug 24-18 (57.1%), Sep (through 9/24) 59-38 (60.8%). Nikki cannot run this herself today: she has no Convex query tool for app databases (only Viktor's `query_app_database` can reach prod), and `repo_run`'s `npx convex run --prod ...` fails because the cloned repo has no deploy key configured — don't retry that path; ask Viktor for a fresh pull instead, or get Stan to hand Nikki a read-only `CONVEX_DEPLOY_KEY` for golden-picks. [computed by Viktor via query_app_database, 2026-09-24]
- **Factor Backtest (2026-08-24, 1,965 games):** Only real signal is **lineup completeness** (fuller side 57.3%, z=2.9), but failed strict walk-forward logloss and markets price lineup news, so kept paper-only. Dead factors: bullpen fatigue, games-last-7d, day-after-night. Getaway day is on the watch-list; glamour fade needs a bigger price archive.

## Data Sources, APIs & Gotchas

- **ESPN API:** `site.api.espn.com` returns 403 when called from Convex servers. Always use the fallback chain (`site.web.api.espn.com` → `site.api.espn.com` → `cdn.espn.com`) with browser-like headers.
- **The Odds API:** Team names match ESPN `displayName` exactly (`away@home` join is safe). One proxy call with full `url=` covers the slate multi-region (`regions=us,eu`).
- **Stale-Quote Guard (2026-08-30):** Books emit garbage prices (+1532/+2900/+5900 on coin-flips) which added phantom units. In `convex/mlb.ts`, `isStalePrice` rejects quotes paying >3× fair payout, plus EV caps (GU 1.0, Value `VALUE_EV_OUTLIER=0.1`) on insert AND latestOdds patches. One-time `purgeStaleQuotes` (`staleQuotePurgeV1`) deleted phantom rows. Clean prod: GU 57-82 −16.8u, Value 40-58 −16.2u [prod, 2026-08-30].

## Development & Deployment Gotchas

- **Convex CLI Deployment:** `bunx convex data ... --prod` can silently hit development if `.env.local` contains a dev deployment key. For production data, only trust programmatic `query_app_database(..., environment="prod")` queries.
- **Codegen Requirement:** After modifying Convex modules, always run `CONVEX_TMPDIR=./tmp bunx convex codegen` (ensuring `./tmp` exists) to prevent build failures on stale `internal.*` types.
- **Public App Routing:** Public route trees (`auth.mode: public`) are not wrapped in a Convex provider by the template — ensure `<ConvexProvider client={convex}>` is added in `PublicAppRoutes.tsx` or `useQuery` throws.
- **Testing & Preview:** `runTest()` assumes an authenticated app; for public apps, use a plain Playwright script (`scripts/picks-test.ts`) after running `bunx playwright install chromium`. Start preview server with `setsid nohup bun run preview &` to prevent termination when tool calls end.
- **Backfill & Retro Honesty:** Past-date refreshes get no odds (The Odds API only returns upcoming events) so backtest rows have no edge/CLV. `mlb:backfill` uses *current* season ratings (retrodictive, flattering the model)—labeled `backtest` and split by `liveFrom`. Do not quote backtest numbers as live performance.
- **Data Shape Lag:** Prod rows written before a model change keep old shapes until a refresh runs (throttled 10 minutes). After deploying model changes, trigger a refresh before verifying prod.

## Open Items & Next Steps

- Complete migration from the legacy hosting platform to Stan's own GitHub + Firebase Hosting + Convex account.
- Expand price archives for glamour fade analysis.
- Evaluate getaway day factors as a potential predictive signal.
