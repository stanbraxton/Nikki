# Stan's idea backlog

## Rule
Discussion-only until Stan explicitly says build. Retain research and reasoning here for future reference.

## Pest control compliance SaaS ("GreenCollar Pest" concept) — PARKED [Stan, 2026-08-17]
Stan's background: former owner of Green Collar Pest Control and Bed Bug Gurus; former state-certified Life Safety Inspector. He raised this idea and wanted discussion only; he then said "put this on the list of projects possible."

### Regulatory layer (verified 2026-08-17, Ohio Dept of Agriculture, ORC 921)
- Pesticide Business license: annual, expires Sept 30; insurance Certificate of Insurance (COI) must stay current on file with ODA (OAC 901:5-11-07/-10 coverage rules).
- ≥1 licensed Commercial Applicator required per location; category system (category 12 = WDI — wood-destroying insect); recertification credits required.
- ORC 921.14 / OAC 901:5-11-10: every pesticide application must be recorded; 3-year retention; records must be available to the ODA director on demand; a copy must go to the customer within 30 days unless a signed waiver is on file.
- WDI real-estate inspections: business license + category-12 applicator required even when no treatment occurs; produces an official report as the product.
- Public prospect roster: ODA licensed company search at https://www.planthealthrenewal.agri.ohio.gov/apps/LicensedCompanySearch (plus an applicator search) — same legal-prospect-list play used for the fire eLicense idea.

### Product shape
Not a filing engine (unlike the oil & gas concept): auto-generated application records per job, automatic 30-day customer-copy delivery, WDI report generation, and expiry alerts (business license Sept 30, COI, each applicator's certification + CE credits). The fear that sells: a lapsed license or COI means the business is operating illegally.

### Architecture note
Roughly 80% reuse of the RedCollar chassis: customers → properties → recurring service programs → tech visits → records → deficiencies/quotes. Likely best positioned as a second trade built on RedCollar's existing foundation rather than as a fresh app.

### Competitive pushback (given to Stan 2026-08-17)
This is the most crowded of the verticals considered. Existing players: PestPac (~$150/user/month, deep state DOA reporting), FieldRoutes (owned by ServiceTitan, $350/month per 1,000 customers), Briostack, GorillaDesk ($49–149/month, owns the 1–2 truck shop segment), Jobber, Workiz. The low end is already well served cheaply — unlike the fire compliance space. The possible wedge is no-per-seat pricing plus compliance depth, but this would be a knife fight. Also, this would be a 4th vertical while RedCollar is still mid-build. Recommendation: shelve it and revisit as a RedCollar second trade later.

## Water well drillers & pump service SaaS — PARKED [Stan, 2026-08-17]
This was #3 in the Aug 16 top-10 industries ranking ("close second — practically a WellCollar reskin"). Stan asked for it to be explored; the verdict was downgraded after research.

### Regulatory/data layer (verified 2026-08-17, Ohio)
- ORC 1521.05: every constructed well requires a well log filed with the ODNR Division of Water Resources within 30 days ($20/log); sealing reports required too. This is a true recurring filing requirement (closer in nature to the WellCollar model than the pest control idea).
- ORC 3701.344 / OAC 3701-28-18: private water systems contractors must register annually with the Ohio Dept of Health plus carry a surety bond; local health departments permit and inspect per well.
- Public data available: ODNR Ohio Water Well Database (searchable logs going back decades); ODH downloadable roster of all registered contractors (a usable prospect list).
- Market size: ~7,622 US businesses, $9.9B market, no firm holds >5% share (IBISWorld 2026).

### Why downgraded — a competitor kills the "open field" premise
**DrillerDB** (drillerdb.com): built originally by a Wisconsin drilling company for its own use, then productized. Offers field well logs with offline sync, state report auto-fill (413 templates covering all 50 states), scheduling, inventory, GPS, AI-generated quotes from local well data, and QuickBooks sync. Pricing: $129/$349/$799 per month plus $15–25/user add-ons. NGWA Business PRO (the trade association's ERP offering) is also in the space. DrillerDB has the founder-authenticity story that this idea would otherwise be faking.
Remaining possible wedges: flat no-per-seat pricing; deep Ohio-specific import of a contractor's own historical ODNR logs. These are real but represent a knife fight.

### Verdict
Dropped below the #2 septic idea and the #4 UST idea (no known DrillerDB-equivalent competitor found there yet — unverified). Lesson recorded: always re-verify the "nobody's looking" premise with a competitor search before promoting any ranked idea toward a build decision.

## Septic pumping / onsite wastewater SaaS — VERIFIED, PREMISE FAILED [2026-08-17]
This was promoted to #1 on the bench after the water-well downgrade; Stan said "proceed," but the verification pass killed it before any build began.
- Ohio regulatory layer (real): OAC 3701-29-03 — installers, service providers, and septage haulers must register with EACH local board of health (i.e., per-county registration), expiring Dec 31, requiring $500k general liability insurance, testing, and 6 continuing-education hours per year. OAC 3701-29-19 — county Operation & Maintenance (O&M) programs track pump-out and service intervals. Multi-county registration tracking is a genuine pain point for operators. Market size: $11.4B, 5,718 US businesses (IBISWorld).
- Competitors found: **ServiceCore** (private-equity-backed, $54M from Mainsail, covers portable toilets/septic/dumpster, ~$200+/month per truck, annual contracts, demo-gated sales); **Tank Track** (family-owned since 2013, $149/month, month-to-month); **PumpDocket** (launched 2026, bootstrapped, $99/month for 1-3 trucks, month-to-month, NO per-user fees, self-serve signup, 50-state compliance profiles with cited sources). PumpDocket is essentially our exact thesis, already executed by a fresh bootstrapped competitor.

## USTs / BUSTR compliance SaaS — VERIFIED, PREMISE FAILED [2026-08-17]
- Ohio regulatory layer (real): BUSTR (State Fire Marshal), OAC 1301:7-9 — underground storage tank (UST) registration, Class A/B/C operator training with certificates kept on file, walkthrough inspections, and financial responsibility documentation.
- Competitors found: **TankAware** ($99/site/month, unlimited internal users — meaning the flat-pricing wedge is already taken); **EKOS** (integrated with automatic tank gauges, enterprise-focused); Titan Cloud and others further up-market. Both the low end and the flat-pricing angle are already occupied.

## Bench status after verification passes [2026-08-17]
Summary: Fire idea is HELD (folded into → RedCollar). Water well idea FAILED (killed by DrillerDB). Septic idea FAILED (killed by PumpDocket/ServiceCore). UST idea FAILED (killed by TankAware). Overall record: 1-for-4 (only fire survived, as a fold-in).
Standing lesson: every "nobody's looking" niche now appears to have a native player already serving it; any edge must come from execution quality plus Stan's personal credentials plus state-data depth — not from assuming an empty market.
Remaining unverified ideas still on the bench: propane, FFL (firearms licensing), small water systems, dump trucks, cemetery, salvage. Verify each with a competitor search before generating any enthusiasm for them.

## Ohio public-data candidates — UNVERIFIED bench adds [from app discussion thread, 2026-08-17]
These came from an "Ohio Public Opportunities" discussion. Filter used to select candidates: money-linked decision, painful today, recurring need, reachable buyer, and alignment with Stan's personal edge. None of these are verified yet — a competitor search is still pending for each; remember the 1-for-4 lesson above before getting excited about any of them.
- **EPA compliance radar** — Ohio EPA permits/violations data could generate leads for small manufacturers/facilities trying to avoid fines. Follows the WellCollar playbook and fits inspector-background DNA. Considered the strongest edge of this batch.
- **New-business lead engine** — Ohio Secretary of State daily business filings could generate leads for insurance agents, banks, and payroll companies. Crowded nationally; weak edge for Stan.
- **Contractor bid-match** — Ohio Buys plus county procurement alerts, targeted at small trades; possible veteran-owned-business set-aside angle. Competitors identified: GovWin, BidNet (competitive depth not yet verified).
- **Restaurant inspection/compliance** — county health district data (which is fragmented, creating a potential moat) could serve restaurant owners. Low personal edge for Stan.
- Ideas killed on sight (not worth pursuing): unclaimed-funds finders (fee caps, one-shot revenue, reputation risk); raw public-data resale (no moat).

### Direction discussed 2026-08-17
Recommendation given: pursue **WellCollar state expansion (West Virginia + Pennsylvania, same geological basin, cross-border operators) rather than starting any new industry**, paired with a stronger Ohio sales push; all new-industry ideas stay benched in the meantime. Stan has not yet decided on this direction.
