# StateScout — venture memo, pitch and outreach

## Thesis [2026-08-17]
Stan's thesis: parse state databases that everyone must file into, and find patterns that predict events (business collapse, distress, risk) before the filers themselves know. Codename: StateScout.

## Verified source inventory (Ohio)
| Source | Access | Scale | Join keys | Status |
|---|---|---|---|---|
| ODNR oil & gas wells | ArcGIS REST + RBDMS Access DBs | ~250k wells | API no, operator | HAVE (from prior WellCollar project) |
| ODNR water well logs | ArcGIS REST DGS_Services/Ohio_Water_Wells | 654,346 logs + 114,785 sealing reports | WELL_LOG_NO, DRILLER_ID/NAME, county, dates | LIVE ✓ queried |
| EPA SDWIS (drinking water) | Envirofacts REST API, PRIMACY_AGENCY_CODE=OH | all OH systems + violations | PWSID | LIVE ✓ queried |
| Ohio Secretary of State (SoS) business filings | FREE monthly report downloads: new filings, dissolutions, cancellations, reinstatements, mergers (2nd Sat/month) | statewide | charter no, entity name, statutory agent | CONFIRMED (site was in maintenance at probe time) |
| BUSTR USTs (underground storage tanks) | "List of USTs" + "List of Environmental Releases" downloads via SFM (State Fire Marshal) portal | all OH tanks | facility no, address | EXISTS — JS portal, needed a browser-automation job |
| ODH septic + private water contractors | downloadable roster | statewide | name, county | EXISTS — page blocks plain curl requests, needed a browser-automation job |
| ODA pesticide businesses | LicensedCompanySearch app | statewide | license no | LOADS ✓ scrapeable |
| SFM fire protection certs (eLicense) | Generate Roster export | ~156–250 companies | cert no | KNOWN (from prior RedCollar research) |

## Demonstrated signal #1 — driller collapse (real data) [2026-08-17]
Water well logs by driller, 2023 vs 2025: market overall GREW (6,866→7,513 filings), yet 3 of 67 active drillers (≥20 logs/yr) collapsed by more than 60%:
- Denny Herr Well Drilling: 37→10
- Turner Drilling: 34→6
- HAD Inc: 25→9

These are acquisition/failure candidates visible ~18 months before any public event. Nobody currently sells this signal.

## Ranked opportunities (by who pays)
1. **Roll-up deal radar** — sell decliner/distress signals (filing collapse + SoS dissolution/cancellation + license non-renewal) to PE firms/consolidators and strategic buyers. Pricing idea: $2–10k/mo per seat. Demo already works.
2. **Vertical Scout leads** — "competitor collapsing near you / new permits near you" sold to trades companies. Also feeds the WellCollar+RedCollar Scout products for free.
3. **Risk radar for lenders/insurers** — UST age × release history × inspection lapses = release-risk score per station (needs the BUSTR pull; done, see below).
4. **Main Street compliance bureau** — long-term identity graph joining SoS + all license rosters + violations. This is a years-long effort; the other three opportunities are its building blocks.

## Next steps (as of first memo, before build)
- Browser job: pull BUSTR UST list + releases list; ODH contractor roster. (Done — see harvest below.)
- Pull SoS dissolution/cancellation monthly reports once site is back online.
- Build second demo: UST release-risk score on real tanks. (Done — see below.)
- Pick first paying customer type before writing any production software.

## BUSTR harvest — COMPLETE [bustr portal, 2026-08-17]
- `data/downloads/Active USTs.xlsx` — 21,443 active tanks / 6,936 facilities (install date, construction, owner, lat/lon).
- `data/releases_all.json` — 45,632 environmental releases (status codes: NFA/DIS/CLO/CSN/NFC = closed; RPT/CON/T1S/RAP/TR1/TR2/T2E = active/open).
- `data/permits_all.json` — 50,350 UST permits (AssetNumber = facility number; used as a proxy for upgrade investment).
- Join keys: release `FacilityReleaseNumber` prefix = UST `Facility Number`; permit `AssetNumber` = facility number.
- **Gotcha:** the export-button path FAILS for big grids ("Records more than Max allowed limit" → falls back to email-only delivery).
- **Working method:** replay the Kendo grid's dataSource in-page via browser automation: `jQuery(grid).data('kendoGrid').dataSource.query({page:n, pageSize:2000})` then `.view().map(r=>r.toJSON())`. This pages the server API using the page's own CSRF session. Note: the automation's JS evaluation returns JS Dates as datetime objects, so dump with `json.dump(..., default=str)`.

## Demo #2 — UST release-risk score [computed 2026-08-17]
Score formula = age(≤40) + bare-steel/unknown construction(10/tank, capped ≤3) + prior releases(7 each, capped ≤4) + open/unclosed release(15) + no permits since 2021(5).
- 3,842 facilities have tanks ≥30 years old.
- 5,090 facilities have a release not yet closed out (per state records).
- Top-100 highest-risk concentration by county: Cuyahoga 22, Lucas 9, Montgomery 6, Franklin 6, Hamilton 5.
- Top named facilities: Portsmouth Gaseous Diffusion Plant (30 releases); Lebanon Correctional (66-year-old tanks, 13 releases); HOP-IN Wood Co (60-year-old tanks, 12 releases) — all three show zero permit activity since 2021.
- Output file: `data/risk_top500.json` (top 500 scored facilities).
- Buyers this signal points at: environmental insurers/underwriters, lenders on gas-station commercial real estate, remediation contractors, PE firms rolling up convenience-store chains.

## Harvest completion [2026-08-17 evening]
- **SoS dissolutions** (source: hb48.ohiosos.gov): 3,978 dissolved LLCs, 06/01–08/17/2026 → `data/sos/dissolutions_jun_aug2026.json` (fields: entity number, name, status, effective date). Breakdown: June 1,901 / July 1,420 / August-to-date 657. All records verified unique. Scraped ~398 pages at 10 rows/page via ASP.NET postback pagination (the "Next Page »" link).
- **ODH STS (sewage treatment systems) bond lists** (rosters dated 8-14-26), downloaded to `data/odh/`: `multi_install.pdf` (363pp), `service_provider.pdf` (143pp), `septage_hauler.pdf` (116pp), `cancellations.pdf` (2pp — bond-cancellation notices, itself a distress signal). Source pages under odh.ohio.gov `/wps/portal/gov/odh/know-our-programs/sewage-treatment-systems/{forms/sts-bond-lists,forms/stsbondcancellation,media/...}`.
  - Gotcha: this site REQUIRES a full browser user-agent string like `Mozilla/5.0 (Windows NT 10.0; Win64; x64)` — a short "Mozilla/5.0" string gets a 404.
  - Gotcha: PDF hrefs on the page are single-quoted, of the form `/wps/wcm/connect/gov/...pdf?MOD=AJPERES...`.
- **SoS TXT monthly reports** (publicfiles.ohiosos.gov): STILL returning 403/maintenance as of [2026-08-17]. Retry later; the hb48 dissolutions source covers the dissolution signal in the meantime.

## Progress checklist (todo.md)
- [x] Probe access (confirmed BUSTR + ODH both needed browser automation)
- [x] Samples pulled: water wells, SDWIS live
- [x] Join keys documented (see inventory table above)
- [x] Demo signal built: driller collapse
- [x] Memo v1 written

---

# Pitch — Buyer Test #1: UST Risk Intelligence (Ohio)

Product artifact: `ohio_ust_risk_teaser.pdf` (white-label 2-page teaser). Underlying data: `../data/risk_top500.json`, with the full scored set reproducible from `../data/`.

## Target list [researched 2026-08-17]
**Wave 1 (recommended, and the wave that was actually sent — see below):**
1. **Axon Underwriting LLC** — UST-specialist MGA (managing general agent), based in Somerville, NJ. Named contact sourced from EPA's official UST Financial Responsibility (FR) provider list: Maddie Hazelgrove, mhazelgrove@axonu.com, 908-458-9434. Rationale: small shop, likely fast to respond.
2. **Great American Insurance / TankAssure** — dedicated storage-tank pollution insurance program; environmental division HQ'd in Cincinnati, OH. An Ohio-data pilot is a natural fit given the local presence.
3. **Ohio Petroleum Underground Storage Tank Release Compensation Board (petroboard.org, "PUSTRCB")** — the state fund that insures losses above the $55k/$11k deductible for every Ohio tank owner (per ORC 3737.91). They carry the tail risk on the entire population StateScout scored.

**Wave 2 (not yet contacted):** Chubb TankSafe (automated UST platform), PartnerOne Environmental (MGA), CEI Environmental (tank-specialist agency), Argo Environmental, Berkley Environmental.

**Adjacent buyers (later-stage):** SBA gas-station lenders (a Phase I Environmental Site Assessment is required on every such loan), environmental consultants (e.g. Partner ESI, LightBox).

## Why insurers first (Stan-approved rationale) [2026-08-17]
Insurers currently rate risk based on tank age/construction/release history that is self-reported on applications. StateScout's dataset is regulator ground truth instead: 21,443 tanks, 45,632 releases, 50,350 permits. 5,090 facilities have an unclosed release — i.e., undisclosed open corrective action sitting in insurers' live books today.

## Status as of first pitch draft
- Teaser PDF built (facility names redacted in the teaser; full names available in the licensed/paid dataset).
- Outreach email drafted but NOT SENT, pending Stan's approval (external-communications rule — always get Stan's sign-off before sending anything external).

## Buyer briefing [2026-08-17]
Document: `buyer_briefing.pdf` (built via `briefing.py`). Contents:
- Market mechanics: 40 CFR 280 Subpart H federal Financial Responsibility mandate; Ohio's 2-layer system — PUSTRCB charges flat per-tank fees ($350/$550), with $55k/$11k deductibles and NO risk-based pricing; private insurers cover the deductible layer plus fund-denial cases; fund eligibility requires fire-marshal compliance at the time of release.
- Target profiles: Axon's EZ-TANK = no-application, database-driven quoting with blind-match offers; TankAssure = portal honor-system binding, $225 minimum premium, based in Cincinnati; PUSTRCB = angle is reserving/forecasting, expect slow government procurement.
- 10 objections with prepared answers.
- 3 conversation rules for any live discussion: never explain how the data was collected; never quote a price; don't oversell the score.
- Underlying research dumps were saved to temp files during prep (not preserved long-term).

## Wave-1 SENT [2026-08-17 ~21:00 ET, Stan-approved]
All three emails sent from stan@greencollarindustries.com, with attachment `Ohio_UST_Risk_Overview.pdf`, sent via raw MIME through a proxy-post method. Note: the first attempt to send via the normal email-with-attachments action failed with an ENOENT error — this is a known gotcha with that email-sending integration when attachments are involved; the raw-MIME-via-proxy_post workaround was used successfully instead.
- Axon: Maddie Hazelgrove <mhazelgrove@axonu.com> (message ID 1a01260eafcdff6b)
- TankAssure: Richard Viglianese, DVP Automated Underwriting <rviglianese@gaig.com> (message ID 1a0126114d3a506d)
- PUSTRCB: Starr Richmond, Executive Director <srichmond@petroboard.org> (message ID 1a0126146ad66913)

Other contacts found during research (not yet used): TankAssure — Heather Boyd hboyd@gaig.com, TankAssureSubmissions@GAIG.com; PUSTRCB — info@petroboard.org, (614) 752-8963.

**Next step:** monitor for replies. If any recipient accepts the 50-facility challenge, match and score their list using the `data/risk_top500.json` pipeline.

## Wave-1 outreach email drafts (as prepared and sent) [prepared 2026-08-17]
From: stan@greencollarindustries.com · Attachment: `ohio_ust_risk_teaser.pdf` · Sign-off: "Stan" plus the existing Gmail signature, appended automatically via the send action's Signature field — do NOT hand-type a signature when sending on Stan's behalf.

Style rule (Stan's preference): each email passively pre-empts ONE likely objection ("You may feel..." → "However...") and offers a free 50-facility blind challenge.

### 1. Axon — Maddie Hazelgrove <mhazelgrove@axonu.com>
Subject: "A free bet on your Ohio tank book — 50 facilities, our data vs. yours"

> Hi Maddie,
>
> I run a data company in Ohio. We've assembled the state's complete UST record — every active tank (21,443), every release ever reported (45,632), and every upgrade permit — into a release-risk score for all 6,936 active facilities in the state.
>
> You may feel Axon already has data-driven underwriting covered — EZ-TANK is built on it. However, we're an upgrade, not a conversion. Your database validates that tanks exist and what they are; ours adds what the regulator knows: 5,090 Ohio facilities currently have a reported release the state has never closed, and the highest-risk decile shows zero upgrade-permit activity in five years.
>
> So here's a challenge, at no cost to you at all: pick any 50 Ohio facilities you insure or quote. We'll return our risk score and the state's release status for each, and you judge what we knew that you didn't.
>
> One-page overview attached.
>
> Stan
> [Gmail signature appended]

### 2. Great American TankAssure — Richard Viglianese <rviglianese@gaig.com>
Subject: "5,090 Ohio facilities have a release the state never closed — is any of your book on the list?"

> Hi Richard,
>
> I run a data company here in Ohio. We've assembled the state's complete UST record — every active tank (21,443), every reported release since the program began (45,632), and every upgrade permit — into a release-risk score for all 6,936 active facilities in the state.
>
> You may feel the TankAssure application already asks about open remediation cases — it does. However, the applicant answers it, and the state record says 5,090 Ohio facilities have a release that was never closed. Some slice of any Ohio book answered "no" to a question the regulator's own file answers "yes."
>
> I'd like to offer a challenge, at no cost to you at all: choose 50 Ohio facilities from your book or your quote flow. We'll return our risk score and the state's release status for each, and you judge what we knew that your application didn't. Given what you've built with automated underwriting, I think you'll see immediately where this plugs in — a verification layer that keeps the portal fast.
>
> One-page overview attached.
>
> Stan
> [Gmail signature appended]

### 3. PUSTRCB — Starr Richmond, Executive Director <srichmond@petroboard.org>
Subject: "A claims-frequency forecast for the Fund — built from the state's own UST records"

> Dear Ms. Richmond,
>
> I run an Ohio data company. We've assembled the state's complete UST record — 21,443 active tanks, 45,632 reported releases, and 50,350 upgrade permits — into a facility-level release-risk score for all 6,936 active facilities the Fund stands behind.
>
> You may feel the Board already has access to these records — it does. However, what doesn't exist today is the joined, scored view: 3,842 facilities operating tanks 30 or more years old, 5,090 with a release never closed, and the highest-risk facilities showing no reinvestment in five years. For a fund that charges every tank the same flat fee, that is tomorrow's claim volume, visible today.
>
> I'd like to offer a demonstration at no cost to the Board: we'll deliver the highest-risk facilities statewide with the score fully documented, for your staff to evaluate for reserving, forecasting, and compliance outreach — protecting both the Fund and Ohio's tank owners.
>
> One-page overview attached. I'd welcome fifteen minutes with you or your staff.
>
> Respectfully,
>
> Stan
> [Gmail signature appended]

## Open items / things to track for Nikki
- Watch for replies to the three sent emails (Axon, TankAssure, PUSTRCB) and flag them to Stan promptly.
- If a 50-facility challenge is accepted by any recipient, run their facility list through the `data/risk_top500.json` scoring pipeline and prepare results.
- Remember the external-communications rule: never send outreach on Stan's behalf without his explicit approval first.
- Remember the attachment-sending gotcha: prefer the raw-MIME-via-proxy_post method for emails with attachments, since the standard send-with-attachments action has failed with ENOENT before.
- SoS TXT monthly report source was still down as of 2026-08-17; check again for the dissolution/cancellation dataset refresh.
- Wave 2 targets (Chubb TankSafe, PartnerOne Environmental, CEI Environmental, Argo Environmental, Berkley Environmental) and adjacent buyers (SBA gas-station lenders, environmental consultants like Partner ESI and LightBox) have not yet been contacted — available as next-round targets once Wave 1 results come in.
