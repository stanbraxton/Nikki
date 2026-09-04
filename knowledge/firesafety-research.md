# Fire-safety inspection market research (RedCollar)

## Overview and Core Strategy [2026-08-16]
RedCollar is positioned as "the compliance brain for 1–10 tech fire protection shops." The strategic playbook applies the WellCollar model to fire and life-safety ITM (Inspection, Testing, and Maintenance) contractors. 

**Non-negotiable house rules:**
- Custom domain locks the browser address bar. No legacy hosting platform branding anywhere.
- Flat pricing only — NO per-user or per-tech fees (this is the core wedge against competitors).
- Public state data is used extensively, but the source is never named in the UI (referred to neutrally as "state records").
- External records link by license or ID number only, never by name matching.

**Target customer:** Owner-operator fire protection companies with 1–10 technicians performing extinguisher, alarm, sprinkler, suppression, or hood ITM. The primary buying triggers are drowning in paper reports, Brycer/TCE filing fees, missed recurrence dates, and AHJ (Authority Having Jurisdiction) report rejections.

## MVP Feature Set and Build Roadmap [2026-08-16]
The application is structured into five core milestones on a React + Convex tech stack, featuring multi-tenancy by company, admin HTTP routes for roster imports, and a PWA manifest with offline caching.

1. **Compliance Schedule Engine (M1 - The Moat):** Encodes NFPA 10, 25, and 72 frequency tables (monthly, quarterly, semi-annual, annual, 5-yr, 6-yr hydro, etc.) across a Customer → Building → System → Device hierarchy. Generates an auto-calculated revenue calendar where every device's next-due dates roll up into a work forecast, backed by an overdue dashboard and customer reminder emails sent on the shop's letterhead.
2. **Field Inspection App (M2):** Mobile-first PWA with offline capabilities. Includes per-standard checklists starting with NFPA 10 (extinguishers) and NFPA 72 (alarms), expanding to NFPA 25 (sprinklers) in v1.1. Supports pass/fail per device, photo capture, deficiency logging with a code citation picker, barcode/QR device tagging, and automatic background sync when reconnected.
3. **AHJ-Grade Reports (M2):** Formats PDF reports matching AHJ expectations—featuring a cover sheet, system summary, device detail, deficiency list with ORC/NFPA citations, and technician license numbers plus company certification numbers printed on every report. Includes one-click email to building owners and export packs for Brycer TCE/IROL batch queues.
4. **Deficiency → Quote Pipeline (M3):** Failed inspection items flow directly into a quote builder (parts + labor). Approved quotes convert to work orders and completion reports, with invoicing handled via Stripe or QuickBooks CSV exports.
5. **State-Data Intelligence (M4):** Imports state fire marshal license rosters (starting with Ohio company and tech certs, refreshed on schedule) to trigger tech license lapse alerts. Scout provides a county map and list of certified competitors combined with public occupancy records to generate prospecting lists of "buildings likely overdue near you."
6. **Help Center & Assistant (M5):** Nikki-pattern help articles covering NFPA frequencies, AHJ submission rules, and state certification requirements, integrated with in-page help links and assistant Q&A powered by the articles.

**Explicitly excluded from MVP:** GPS fleet tracking, payroll, inventory/PO, monitoring-central-station integration, multi-region rosters beyond Ohio, native app-store apps, and direct Brycer API integrations.

## Pricing and Naming [2026-08-16]
**Pricing (Locked):** Never charge per-user, per-tech, or per-seat ("we will never charge you for a login"). Tiers band by devices under management, mirroring the WellCollar well-count model:
- **Solo:** $99/mo — up to 500 devices
- **Crew:** $199/mo — up to 2,500 devices
- **Shop:** $349/mo — up to 7,500 devices
- **Pro:** ~$0.05/device/mo above 7,500 (minimum $375/mo)
- **Onboarding:** Flat $0 / $250 / $500 for white-glove data import.
- **Terms:** No contracts, month-to-month. All features included at every tier with unlimited reports and users. Band sizes are provisional until real-world shop device counts are observed, but the banded structure is locked.

**Name & Domains (Locked 2026-08-16):** 
Named *RedCollar* (fire = red, continuing the Green Collar / WellCollar family). Verisign RDAP checks conducted on 2026-08-16 show `redcollar.com` is taken (NY digital agency `redcollar.co`/`.ru`, `redcollar.io`, and Russian trademark exist). Available domains include `redcollarfire.com`, `redcollarhq.com`, `redcollarsoftware.com`, `getredcollar.com`, and `tryredcollar.com`. No live US trademark conflicts found in fire/field-service software. Stan approved purchasing `redcollarfire.com` (primary) and `redcollarhq.com` (defensive) on Squarespace, deferring purchase to a later time.

## Competitor Teardown and Market Gaps [2026-08-17]
Market research across incumbent and emerging platforms reveals key insights into the contractor landscape:

- **Inspect Point:** The fire-specific incumbent (founded 2014, Troy NY, backed by $28M Mainsail growth equity in 2022). Deep code libraries, iOS-only offline app with barcode scanning, AI inspection assistant, and Brycer/TCE sync. Priced around $129/user/mo with a 2-tech minimum and annual contracts. Criticized for opaque pricing, iOS-only restriction, and a 3.8/5 Capterra rating among small shops.
- **ServiceTrade:** Commercial service platform with a strong fire vertical (best for 20+ techs). Features NFPA forms, repair quoting, customer portal, QuickBooks sync, and Brycer integration on premium tiers. Priced per technician ($59–139/tech/mo) with compliance features gated in upper tiers.
- **BuildingReports:** Compliance reporting network using ScanSeries apps (iOS/Android). ComplianceCenter is free to AHJs, funded by contractor service member fees (~$99/user/mo). Strong on compliance, weak on back-office quoting and invoicing.
- **Uptick:** Purpose-built fire platform expanding from Australia. Complete operational model (asset lists, floorplans, scheduling, quoting, billing, inventory, portal). Priced around $180/user/mo with unlimited logins, but slowed by a 2-month onboarding process and maturing US localization.
- **FireFlow (`getfireflow.com`):** A direct clone of RedCollar's thesis—built for 1–10 tech fire contractors with NFPA checklists, truck-side deficiency quoting, branded PDFs, offline mobile, and $99/mo flat pricing with no contracts. This validates the niche while proving Nikki must differentiate on depth (schedule engine + true AHJ reports + TCE readiness).
- **Generic FSM (Housecall Pro, Jobber, Workiz):** What small shops actually settle for ($39–329/mo). Transparent pricing, but completely lacking NFPA templates, device models, recurring ITM engines, and AHJ reports. They represent the true competition for 1–5 tech shops.

**Identified gaps in RedCollar:** Stripe payment collection, true offline mutation sync (PWA cache alone is insufficient), recurring inspection agreements/agreements priced from device lists with renewal alerts, customer portal, QuickBooks sync, TCE/Brycer submission support, and sprinkler (NFPA 25) trade support.

## NFPA Standards and AHJ Compliance Requirements [2026-08-17]
Compliance reporting requires strict adherence to national standards and state regulations:

- **NFPA 72 (Fire Alarm):** Chapter 7 §7.8 defines official forms (Record of Completion and System Record of Inspection and Testing). Requires property info, monitoring details, system description (control unit make/model, software revision, primary/secondary power specifications including voltage, amps, breaker location, battery calculations), pre/post-test notifications, testing results across devices and power supplies, restoration records, and certification signatures.
- **NFPA 25 (Water-Based Systems):** Annex B forms and frequency-based checklists (weekly/monthly gauges/valves, quarterly alarms, annual tests, 3-yr dry trip, 5-yr internal pipe/gauge/flow tests). Requires system-level records (e.g., treating a wet system riser as the asset rather than individual heads).
- **NFPA 10 (Extinguishers):** Monthly visual checks, annual maintenance, 6-year internal dry-chem maintenance, and hydrostatic testing. Service tags require service dates, service type, signature, and the technician's license number (explicitly mandated by Ohio law).
- **Ohio Specifics:** ORC 3737.65 and OFC 918 require a State Fire Marshal (SFM) certificate for anyone servicing fire protection equipment for profit. You must store each technician's SFM certificate number and expiration date, print them on reports and tags, and trigger lapse alerts. Reports must be submitted to local building/fire officials.
- **Third-Party Portals (The Compliance Engine / Brycer):** Free to AHJs, funded by contractor filing fees (~$15/report). Contractors upload a single typed PDF per system report within 14 days. RedCollar must generate complete, typed, single-PDF, NFPA-format reports to support seamless compliance uploads.
- **Adjacent Trades:** NFPA 96 kitchen hood cleaning/inspection (1/3/6/12-month cycles) and emergency lights/exit signs (monthly 30-second test + annual 90-minute battery test per IFC/NFPA 101) represent high-margin, low-complexity add-on services for small shops.

## Shop Discovery Kit and Interview Playbook [2026-08-17]
When interviewing 3–5 local fire protection shops (1–10 techs, 30–45 min sessions), use the discovery kit to uncover operational realities:
- **Approach:** Do not pitch early. Ask about the *last* inspection performed rather than hypothetical routines. Chase money and swearing (missed revenue, double-entry, paper friction).
- **Key Artifacts to Collect:** Blank inspection forms per trade, completed reports (redacted), deficiency quote templates, invoices, recurring agreements, schedule photos, service tags, and AHJ instruction sheets.
- **Listen-for Signals:** 
  - Schedule in head/Excel → validates M1 schedule engine wedge.
  - Delayed deficiency quotes → validates deficiency-to-quote pipeline as the revenue driver.
  - Abandoned legacy software over price → validates banded flat pricing.
  - Evening report retyping → validates automated report generation as a family-time saver.
  - AHJ/TCE submission friction → validates TCE-ready single-PDF export.
  - Android phone usage in trucks → validates PWA/web approach over iOS-native locks.

## Strategic Recommendations for Nikki and Product Evolution [2026-08-17]
Based on market research and compliance teardowns, you should guide RedCollar's development with these priorities:
1. **Add an Alarm System Entity:** Model panel make/model, power specifications, batteries, and monitoring organizations, alongside inspection-level fields for pre/post-test notifications, system restoration, and certification signatures.
2. **Track Technician Credentials:** Store Ohio SFM certificate numbers and expiration dates per user, print them automatically on every report and service tag, and build automated lapse alerts.
3. **Capture On-Device Signatures:** Implement digital signature capture for both technicians and customer representatives to satisfy NFPA certification blocks and TCE proof-of-service requirements.
4. **Provide One-Click PDF Exports:** Enable direct single-PDF report downloads designed specifically for email delivery and Brycer/TCE compliance portal uploads.
5. **Upgrade QR Tags to Service Tags:** Enhance printed QR tag sheets to include service dates, service type, technician license numbers, and signature lines so a single scanned physical tag satisfies both tracking and legal regulation.
