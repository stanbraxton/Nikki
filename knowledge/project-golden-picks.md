# Golden Picks — MLB predictions

Golden Picks is a public web application for baseball fans and bettors, providing recommended MLB team picks per game with win probabilities, confidence ratings, live market odds, and market edge analysis. Built for Stan Braxton, the app aggregates quantitative models, lineup data, and sharp odds to track performance and paper-trade strategic betting edges.

## Overview & Architecture

Golden Picks runs automated refreshes every 30 minutes (via scheduled job `mlb:cronRefresh`, skipping 3–8 AM ET) to grade pending games, sync live odds, evaluate daily picks, and update model ratings.

- **Project name:** `golden-picks`.
- **Access:** Public (`auth.mode: public`), by Stan's explicit choice, built 2026-08-12.
- **Legacy hosting:** the app originally ran on the legacy hosting platform. Production URL there was `https://waudiytksp0jiazo.[legacy-space-domain]`, preview `https://preview-waudiytksp0jiazo.[legacy-space-domain]`.
- **Backend modules (`convex/`):**
  - `model.ts`: core model math (Pythagorean, Log5, HFA, confidence, factors). Main model is a tempered 50/50 current-strength + Elo ensemble (`ensembleHomeProb`, T=0.7, clamp 28–72).
  - `odds.ts`: Odds API integration via tool gateway.
  - `pitchers.ts`: probable starter lines and FIP scoring (cached 12h in `pitchers` table).
  - `underdogs.ts`: Golden Underdogs +EV math (Stan's formulas, verbatim).
  - `teamstats.ts`: reconstructed wRC+, xFIP, and error-per-game estimates from ESPN raw data (cached 12h in `teamAdvanced`); ESPN lacks wRC+/xFIP directly so these are estimates — always describe them as estimates, not validated figures.
  - `mlb.ts`: sync, refresh actions, and core queries (`gamesForDate`, `record`, `teamRatings`); also holds `isStalePrice`, `purgeStaleQuotes`, `FADE_LOOKBACK_DAYS`, `seriesFadeFor`, and `mlb:backfill`.
  - `lineup.ts`: lineup completeness, formula evaluation, and bootstrap seeding (`lineupStarts` from `convex/lineupSeedData.ts`, meta `lineupSeeded`).
  - `daily.ts`: Daily Picks evaluation (`dailyPicks` table).
  - `crons.ts`: scheduled 30-minute refresh jobs.
  - `elo.ts`: Elo rating engine (K=5, HA=15, MOV) and bootstrapping (`elo:bootstrap` seeded from `convex/eloSeedData.ts`, walk-through through 8/23, excluding the All-Star game, marking `RETRO_DATES` 8/16–18 and 8/20–23); applies once per final in `teams.elo`.
- **Frontend pages & components (`src/pages/`, `src/components/`):** `PicksPage`, `RecordPage`, `RatingsPage`, `ValuePage`, `FormulaPage`, and `PickCard.tsx`.

## Nikki/GCP migration (M3, prepped 2026-09-07)

- Standalone fork created and moved to a private GitHub repo `stanbraxton/golden-picks`. Legacy auth/bridge code removed (Convex Auth Password + Resend codes from `no-reply@wellcollar.com`); typecheck/build clean; zero references to the legacy platform remain in the fork. [app, 2026-09-07]
- Firebase Hosting site created (empty): https://golden-picks.web.app.
- Auth keys stored at `projects/nikki/.secrets/golden-picks_jwt.json` (do not print contents).
- Status: public app, no login UI (auth files removed). Odds are fetched via direct calls to The Odds API (`ODDS_API_KEY` set as a Convex env var) — this needs Stan to supply: (1) a Convex prod deploy key, and (2) an Odds API key.
- Migration sequence once keys are in hand: set Convex env vars → Nikki runs `register_app` → `deploy_app` → smoke test → get Stan's OK before any DNS or production cutover.
- The old legacy-hosted copy of the app stays live as a fallback until Stan explicitly retires it.

## Core Models & Formulas

### Main win-probability model
- **Strength:** 65% Pythagorean expectation (exponent 1.83) + 35% win percentage, regressed toward .500 below 60 games played, plus a small last-10 form nudge.
- **Log5 & HFA:** two team strengths combine via Log5, adjusted for home-field advantage (+0.155 log-odds ≈ 54%) and probable-starter edge in log-odds space.
- **Starter quality:** starter FIP regressed toward league average (4.10) by innings pitched (50 IP = half weight); net edge capped at ±0.35 logit (~±8 points).
- **Clamping & edge:** output clamped to 28%–72% because single MLB games are high variance. Edge = model pick probability − vig-free market probability (per-book vig removal, averaged).
- **Model research (backtest, 2026-08-15):** walk-forward backtest over the full 2026 season (1,842 ESPN games; harness `scripts/backtest.py`, fetcher `scripts/fetch_season.py`). Findings:
  - All model-variant differences are tiny; MLB single games are near coin flips (best logloss ~0.689 vs 0.693 coinflip). Current model is worst on the Jun–Aug window (overconfident, logloss 0.6978 > coinflip) but BEST on held-out Jul16–Aug15 (0.68358, 57.6% accuracy).
  - Tested and NOT better out-of-sample: HFA sweep, Pythagorean exponent sweep/Pythagenpat, blend/regression/form sweeps, clamp changes, rolling-window ratings, logistic regression on rating diffs.
  - Elo (K=5, HA=15, MOV multiplier) and the 50/50 current+Elo ensemble (T≈0.6–0.7) are top-3 in BOTH windows — the most robust candidates, which is why they were shipped.
  - Season home win rate 2026: 52.6%; fitted logistic home bias 0.062 logit (~51.5%) vs model HFA 0.155 (~54%) — HFA may be slightly rich, but HFA sweeps didn't beat the shipped value.
  - Bet-log gap identified here too: bets only open while a game is `scheduled` during a page-load refresh, so days with no visitors (e.g. 2026-08-14) logged NO underdog bets. A scheduled refresh (rather than page-load-driven) would fix this plus CLV plus grading — this was later addressed (see CLV section).
- **Results audit (2026-08-15, 172 graded picks / 18 graded underdog bets):**
  - Main-model watch items (not yet statistically significant): away picks at 50–60% ran 45.7% (n=35) vs home 58.8%; picks whose starter edge favored the pick ran 5-10 (33%, n=15) vs neutral 61% — possible pitcher-edge overweighting (already capped at ±0.35 logit). Calibration by probability bucket and confidence tier remained healthy. Live record 18-20 vs backtest 62.7% (backtest flatters, as expected/known).
  - Grading is page-load-driven: if nobody visits, recent dates stay ungraded. To settle them read-only, drive prod with a Playwright script (click Refresh + step through past dates with the left-arrow control); a database query alone cannot trigger the underlying actions.

### Golden Underdogs (second independent model, `/underdogs`)
- Implements Stan's specific formulas verbatim [stan, 2026-08-12]:
  - `ProjRuns_A = 4.4 × (wRC_A/100) × (xFIP_B/4.3) + 0.15 × errors_B`
  - Pythagenport exponent `(totalRuns/2)^0.287`
  - EV = `p × payout − (1 − p)`
  - Quarter-Kelly staking floored at 0
  - Flagged at EV ≥ 3% ⇒ "GOLDEN UNDERDOG PLAY".
- Cards show implied win%, Golden% (implied + EV), and R/R (payout ÷ P(lose)). Playable sides sort by EV then R/R.
- Sharp line = first available of Pinnacle, Lowvig, BetOnline.ag, Matchbook (`regions=us,eu` required to capture Pinnacle, since it's in the EU region).
- **Research & re-audit:**
  - Systematically overconfident: avg pCal 51.4% vs market implied 46.1% vs actual 44.4% (n=18, −0.84u). EV values >20% (up to 38%) are input-error signals, not real opportunities.
  - Underdog re-sim (n=18, too small to be conclusive): shrinking pCal 50/50 toward market-implied turns −0.84u into +0.39u while keeping 13/18 plays; an EV≥8% threshold looked good but is in-sample cherry-picking — the calibration-shrink fix is preferred over a threshold change.
  - **2026-08-24 GU re-audit (n=53 real bets: 21-32, −7.43u, −14% ROI):** on the 34 most recent graded bets, NO variant backtested profitable: as-is −21.7% ROI, 50/50 shrink −39%, EV≥8% −53%, 65% shrink −50.6%. Actual win rate 35.3% vs implied 43.7% — GU edges are currently anti-signal; the 8/15 shrink result did not hold up. Hence GU stays in paper-tracking mode instead of a formula change.
  - **Rule for you:** never present GU plays as +EV validated alpha. Always present them as Stan's formula output, described as estimates/paper tracking, not as proven edge — unless Stan explicitly changes this scope.

### Value Board (`/value`)
- Third slate view (`convex/value.ts`, `valueBets`, `mlb:valueRecord`).
- No forecasting: pTrue = devigged sharp line (Pinnacle first), EV = pTrue × payout(best book price) − (1 − pTrue), flagged at EV ≥ 1%, quarter-Kelly sizing, outlier badge for quotes >10% (stale-quote signal).
- **Caveat:** exchange (Matchbook/Betfair) commission is NOT modeled, overstating EV there by roughly 1–2%.

### Golden Formula (lineup paper model, `/formula`)
- Shipped to production 2026-08-24, shown as an amber "paper-tracking" tab.
- `pHome` = devigged sharp probability + clamp(±4 pts) of `LINEUP_COEF=0.887` × lineup-completeness diff, in logit space.
- Sides at EV ≥ 1% (best book price) → "FORMULA PLAY", logged in `formulaBets` (mirrors `valueBets`), recorded via `mlb:formulaRecord`.
- Requires 9 posted players AND 9 known regulars (≥3 starts) or no formula is shown at all.
- **Lineup data:** pregame lineups fetched from MLB StatsAPI inside `refresh` via `schedule?sportId=1&date=...&hydrate=lineups,team` — the `team` hydrate is required for team abbreviations; lineups post roughly 1–4h pregame; MLB→ESPN abbreviation mismatches are only AZ→ARI and CWS→CHW; doubleheaders matched by nearest `gameDate`. This fetch is try/catch-protected — failure must never halt the overall refresh.
- Lineups are treated as facts, not picks: backfilled onto locked games; starts folded into `lineupStarts` once per final (`games.lineupApplied`).
- **Caveat:** fading the formula (taking the opposite side) performs worse than following it (fade record 36-43, −6.4% ROI vs the formula's own +1.3%) — the opposite side is, by construction, the overpriced side. [prod/re-audit, 2026-08-30]
- **2-week backtest (2026-08-24, Stan-requested):** replayed the shipped formula (sharp devig + 0.887×lcDiff logit, cap ±4pts, EV≥1% at best archived price) over the prod price archive: 68 priced finals across 5 slates (8/12–15, 8/19), boxscore lineups used as posted lineups, walk-forward starts. Script location: a temp working script `formula_backtest.py` using `gp3/mlb_season.json` + `odds_games.json` as inputs (not part of the shipped repo).
  - EV≥1%: 50 plays, 29-21, +6.34u (+12.7% ROI), z≈0.90.
  - EV≥3%: 42 plays, 24-18, +6.42u (+15.3%), z≈0.99.
  - EV≥5%: 28 plays, 16-12, +4.95u, z≈0.94.
  - Lineup-lift-only plays (i.e., plays the Value Board alone would not have flagged): 42 plays, 26-16, +8.16u.
  - Value-board-overlap plays: 3-5, −1.82u.
  - Caveats reported to Stan: sample sizes are tiny and z<1 (results are consistent with luck); archived prices are first-pitch closes that already contain lineup news, so this may overstate real edge; the formula flags 50 of 67 games on at least one side (≈75%), which has a Golden-Underdogs-style "overflag" smell. Verdict: stays paper-only; judge any future claim by live CLV, not this backtest.

### Daily Picks (shipped 2026-08-29)
- One tracked "Pick of the Day" (main model's top-confidence side) and "Value of the Day" (highest-EV non-outlier Value Board play) per day.
- `convex/daily.ts`: `evaluateDaily` runs from `refresh` after `upsertGames`; selection is provisional, locks ~2h before the game's first pitch/slate start, then closes/CLV/grades like the bet logs. Value of the Day skips any play with EV above `VALUE_EV_OUTLIER` (treated as a stale quote).
- `dailyPicks` table; one-time bootstrap (`dailySeededV2`) seeded history from `liveFrom` onward at archived best prices (rows marked `retro`/seeded and excluded from live units).
- Record queries: `daily:dailyRecord`, `daily:dailyForDate`. UI: banners on `PicksPage`, two `DailySection`s on `/record`.

## Bet Logs & CLV Tracking

- `underdogBets` / `valueBets` / `formulaBets`: one row per flagged side, opened only while the game is `scheduled`, at the displayed/`lockedOdds` price.
- Pre-start refreshes update `latestOdds`; the value captured at first pitch becomes `closingOdds`.
- `clv` = payout(locked) − payout(close) per $1.
- Grading runs from the game row alone. All three logs show on `/record`: W-L, flat 1-unit sizing, ROI, and CLV.
- **Initial CLV issue:** early audits found `closingOdds = latestOdds ?? lockedOdds`, and `latestOdds` only updated on a pre-start page-load refresh. Since nobody reliably loaded the page between bet-open and first pitch, all 18 graded bets in the 8/15 audit had CLV exactly 0.0 (beat 0/18). This was reported to Stan 2026-08-15 as needing a scheduled (non-page-load) refresh near game starts to mean anything; this was subsequently resolved by moving to scheduled refreshes.

## Series Fade & Factor Backtest

- **Series Fade, shipped 2026-08-13:** when a favorite loses, in the research dataset it kept losing the *same matchup* at a similar probability for the rest of the series about 53% of the time (season-long team stats barely move night to night, so the model can't see a short-series flip — examples: SD swept MIL 3 straight, TOR beat BOS 3 straight, NYM beat CLE 3 straight, COL beat ARI twice, all while the model kept favoring the same side at 50–68%).
  - Implementation: lookback in `convex/mlb.ts` (`FADE_LOOKBACK_DAYS = 5`, `seriesFadeFor`) — before finalizing a pick's confidence, checks the last 5 days of graded games for this exact pick-side vs. this exact opponent having already lost; if found, drops `confidenceScore` by 20 (relabels the tier), prepends a factors bullet, and stores `game.seriesFade = {priorDate, priorProb}` for a UI badge.
  - Schema: `games.seriesFade` (`v.optional(v.any())`), frozen once the game locks like other pregame fields.
  - UI: amber "Series fade" badge in `PickCard.tsx` header, plus a bullet in the "why" expansion; the `Game` type in `src/lib/picks.ts` carries `seriesFade`.
  - **Record since shipped (2026-08-13) through 2026-09-24:** there is no dedicated record query in the app for this slice — it has to be computed by pulling `mlb:gamesForDate` for every date since 2026-08-13 and filtering rows where `seriesFade` is set, against prod. As of 2026-09-24: live (non-retro), graded-only fade-flagged picks went **83-56 (59.7%)**; including 14 retro-graded rows (7-7), 90-63 (58.8%). For context, all live graded picks in the same window were 262-200 (56.7%) overall — so fade-flagged picks are running *slightly above* overall pick accuracy, not below; the badge is a caution flag, not a signal that the pick usually loses. Monthly: Aug 24-18 (57.1%), Sep (through 9/24) 59-38 (60.8%). [computed 2026-09-24]
  - **Note for you:** you currently cannot recompute this yourself — you have no Convex query tool that can reach the golden-picks app database in prod, and running `npx convex run --prod ...` from a repo clone fails because the cloned repo has no deploy key configured; don't keep retrying that path. Either ask for a fresh pull of the computed numbers, or ask Stan to hand you a read-only `CONVEX_DEPLOY_KEY` for golden-picks so you can query prod directly.
  - **Hypothetical opposite-side betting analysis:** opposite-side $100 flat stakes on the 139 live graded series-fade games in the same window: 56 wins, 83 losses; $7,569.62 gross winning profit less $8,300 in losing stakes = **−$730.38 net on $13,900 staked (−5.25% ROI)**, using the stored *best opposing-side* American moneyline (`bestAwayOdds` when the pick was home, else `bestHomeOdds`). All 139 games had a quote available. Monthly: Aug −$214.64 (42 games), Sep through graded 9/23 −$515.74 (97 games). Important caveats: these are best cross-book lines, not necessarily executable at Stan's actual sportsbook (theScore Bet); exchange commission/limits and execution timing are not accounted for — call this a hypothetical, not an actual bet log. Also, `oddsUpdatedAt` can be later than first pitch while the quote itself is preserved from pregame by `upsertGames`, so don't read `oddsUpdatedAt` as the time of the locked quote. [prod query, 2026-09-24]
- **Factor backtest (2026-08-24, 1,965 games):** the only real signal found is **lineup completeness** (fuller-lineup side won 57.3% of the time, z=2.9), but it failed a strict walk-forward logloss test and markets already price lineup news in, so it was kept paper-only (this is what became the Golden Formula). Factors found to be dead ends: bullpen fatigue, games played in last 7 days, day-after-night-game effect. Getaway-day effect remains on the watch-list (unresolved). Glamour-team fade needs a bigger price archive before it can be tested.

## Honesty Rules (apply these when discussing the app)

- The 2026-08-15 and 2026-08-24 audits found no profitable model variant for Golden Underdogs; GU edges were anti-signal. GU and other formula-type experiments (Golden Formula, Value Board) remain paper-tracking only unless Stan explicitly changes scope.
- Never present Golden Underdogs plays as validated +EV alpha.
- `games.retro` rows were created after a game already started (backfilled); live records must exclude retro rows. The `liveFrom` field is what splits genuinely live rows from backtest rows.
- `mlb:backfill` re-runs past dates using *current* season ratings, and past dates have no real odds (The Odds API only returns upcoming events) — so backfilled rows have no real edge/CLV. Always label these as retrodictive/backtest and never quote them as real historical performance.
- Stale quotes caused phantom gains before 2026-08-30. Fix: `isStalePrice` (in `convex/mlb.ts`) rejects quotes paying more than 3× fair payout, plus EV caps on insert and on `latestOdds` patches (GU EV cap 1.0, Value cap `VALUE_EV_OUTLIER=0.1`). A one-time `purgeStaleQuotes` (`staleQuotePurgeV1`) job deleted the phantom rows on both deployments. After cleanup, prod showed GU 57-82, −16.8u, and Value 40-58, −16.2u. [prod, 2026-08-30]

## Data Sources, APIs & Gotchas

- **ESPN API:** `site.api.espn.com` returns 403 when called from Convex servers (and also from a sandboxed dev environment). Always use the fallback chain `site.web.api.espn.com` → `site.api.espn.com` → `cdn.espn.com`, with browser-like headers.
- **The Odds API:** team names match ESPN's `displayName` exactly, so an `away@home` join is safe. One proxy call (`mcp_pd_the_odds_api_proxy_get`) with a full `url=` parameter covers a multi-region slate; use `regions=us,eu` to capture Pinnacle. See the Odds API integration reference doc for details.
- **Stale-quote guard (2026-08-30):** books occasionally emit garbage prices (e.g. +1532/+2900/+5900 on effective coin-flip games), which had been adding phantom units before the guard was added — see Honesty Rules above for the fix.

## Development & Deployment Gotchas

- **Convex module changes:** after modifying any Convex module, run `mkdir -p tmp && CONVEX_TMPDIR=./tmp bunx convex codegen` — stale `internal.*` types otherwise break the build.
- **Convex CLI deploy targeting:** `bunx convex ... --prod` and `bunx convex data ... --prod` can silently hit the dev deployment instead of prod, because a dev `CONVEX_DEPLOY_KEY` in `.env.local` overrides the `--prod` flag. For production data, only trust results from `query_app_database(..., environment="prod")` (or, going forward, an equivalent verified prod-scoped tool/key).
- **Public app routing:** public apps are not automatically wrapped in a Convex provider — add `<ConvexProvider client={convex}>` in `PublicAppRoutes.tsx`, or `useQuery` calls will throw.
- **Local build env:** build locally with `VITE_...ACCESS_MODE=public bun run build` if `.env.local` says "authenticated" but the app should be public; actual deploys read the app's own space/hosting config file for the real access mode.
- **Testing public apps:** don't use a helper that assumes authentication (e.g. a generic `runTest()`); instead use a plain Playwright script such as `scripts/picks-test.ts`. Install Chromium first if needed: `bunx playwright install chromium`.
- **Preview server:** start it with `setsid nohup bun run preview &` — a plain `&` background job dies when the invoking shell call ends.
- **Text encoding gotcha:** writing TSX via a Python heredoc can turn `±` into the literal escape sequence `\u00b1` instead of the real character — type the `±` character directly instead.
- **Data shape lag:** prod rows written before a model change keep their old shape until the next refresh runs (throttled to every ~10 minutes); after deploying a model change, trigger a refresh before verifying results in prod.

## Open Items & Next Steps

- Complete the migration off the legacy hosting platform onto Stan's own GitHub (`stanbraxton/golden-picks`) + Firebase Hosting + Convex account. Blocking items: Stan needs to provide a Convex prod deploy key and an Odds API key; then follow the migration sequence (env vars → `register_app` → `deploy_app` → smoke test → Stan's sign-off → DNS/cutover).
- Get a read-only `CONVEX_DEPLOY_KEY` (or equivalent) for golden-picks so you can query prod directly for things like the series-fade record, instead of relying on someone else's `query_app_database` runs.
- Expand the price archive to properly test the glamour-team-fade factor.
- Continue evaluating the getaway-day factor as a potential predictive signal.
