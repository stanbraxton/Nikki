# Research method — data analysis, backtests, betting math

Read this before reporting any quantitative result, and especially before calling something
an "edge". Every rule here comes from a real mistake (Golden Picks, 2026-09-24/25): an
in-sample blend showed a "real but tiny" edge that disappeared out of sample, built on
price data that implied free arbitrage on most games.

## Before you compute anything
1. **Sanity-check the inputs first.** Look at ranges, missing values, dates and timestamps.
   For odds: the two sides of a price pair must imply a margin above 0 (implied home + away > 1).
   A negative margin means the data is broken, not that you found free money.
2. **Check timing.** Every input must have been knowable *before* the outcome. Odds or stats
   updated after the start of the event leak the result.
3. **Know your sample.** Report n, the date range, and what you excluded and why.

## Evaluating a model
4. **Out of sample or it doesn't count.** Fit on earlier data, test on later data it never saw
   (time-based split). An in-sample improvement is a hypothesis, not a finding.
5. **Include an intercept** in any regression, and make sure the fit actually converged.
6. **Regression weights are not percentages.** Correlated inputs make "normalize to a %
   split" meaningless. Report the coefficients as they are.
7. **Compare against the market, not against zero.** The market line is the benchmark.

## Deciding if it makes money
8. **Judge by profit after the vig, at prices you could actually have bet.** Log-loss or
   accuracy gains are not money. Simulate the bets out of sample; report ROI and bet count.
9. **Raising the edge threshold should help if the edge is real.** If ROI gets worse as you
   demand more edge, the "edge" is noise or bad data.
10. **Small samples: say so.** Give the uncertainty; a few hundred games can't confirm a 1% edge.

## Reporting
11. Lead with the answer and its main caveat. Separate what you verified from what you infer.
12. If a later check contradicts an earlier conclusion of yours, say so plainly and retract it.
