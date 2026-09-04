# PlainLaw — Ohio small-claims legal help
PlainLaw is a national-brand legal-help product focused on guided small-claims workflows, launching exclusively in Ohio. It is built for individuals navigating small-claims disputes who need reliable, retrieval-first legal guidance without open-ended chat or "robot lawyer" claims.

## Summary
- **Concept & Source of Truth:** Specifications documented in `projects/plainlaw/SPEC.md` [2026-08-14].

## Business & pricing
- **Positioning:** Not a "robot lawyer" or open-ended legal chatbot. AI explains supplied law and text; it does not provide legal advice.
- **Retrieval-First Architecture:** Displays verbatim Ohio Revised Code quotes with official `codes.ohio.gov` links.
- **Exclusions (Early Phase):** Multi-state content, family or criminal law, attorney-facing tools, and open-ended chat interfaces.
- **Pre-Launch Blockers:** Separate LLC formation, E&O insurance, human UPL/disclaimer review, and manual verification of county court workflows.
- **Pricing Tiers:** Free tier, Resolve tier (per-matter pricing as primary monetization), and Pro tier for attorneys (deferred beyond year 1).

## Architecture
- **Tech Stack:** React/Vite + Convex web app (currently hosted on the old platform; slated for migration to Stan's own GitHub + Firebase Hosting + Convex account).
- **Access & Security:** Gated by `workspace_members` [2026-08-14]; fully authenticated mode.
- **Design System:** Warm paper background, ink-navy primary color, brass accent; Fraunces display font, Public Sans body font, and IBM Plex Mono citation font. Disclaimers present on every surface.
- **Routing & Surfaces:**
  - *Public surfaces:* Landing page, `/statutes`, `/statutes/:section`.
  - *Authenticated surfaces:* `/dashboard`, `/matter/:id`, `/decoder`.

## Data model
- **Statutes Table:** Public queries enabled by design; public law serves as the SEO front door.
- **Matters Table:** Stores authenticated user matter workflows.
- **Corpus Data:** Covers ORC chapters 1925 (small claims), 5321 (landlord-tenant), 1345 (CSPA), and 2305 (limitations) spanning 189 sections. Durable export stored at `projects/plainlaw/data/orc_corpus.json` [codes.ohio.gov, 2026-08-14].
- **County Data:** Managed in `src/lib/counties.ts`; each entry includes `sourceUrl` and `verifiedOn` metadata [2026-08-14].

## Key logic
- **Matter Wizard:** 6-step guided workflow producing a complaint draft, county filing checklist, deadlines with `.ics` export, attorney handoff packet, and cited law.
- **Deadline Calculations:** Counterclaim deadline = trial date − 7 days (ORC 1925.02(C)); appeal deadline = 30 days (App.R. 4).
- **Document Decoder:** Supports text pasting, file uploads, and camera scans. Client-side extraction uses `pdf.js`, `tesseract.js`, and `mammoth`, transmitting only extracted text to backend AI actions.

## Integrations & APIs
- **AI Gateway Actions:** `convex/ai.ts` actions `explainStatute` and `decodeDocument` utilize gateway `ai_structured_output` configured with strict UPL guardrails.
- **Corpus Sourcing:** Scrapes official Ohio statutory sources (`codes.ohio.gov`).

## Status
- **Deployment & Access:** App is accessible under authenticated workspace member restrictions [2026-08-14].
- **Testing:** E2E test scripts (`scripts/test-plainlaw.ts`, `scripts/test-plainlaw-ai.ts`, `scripts/test-decoder-upload.ts`) verified passing after M1/decoder milestones [2026-08-15].

## Gotchas & lessons
- **Rate Limiting on Scraping:** Per-section fetches on `codes.ohio.gov` trigger HTTP 429 errors; scraping must target chapter pages instead.
- **Data Cleaning:** Trailing "Last updated..." footers must be stripped from scraped statutory text.
- **Compliance:** Strict UPL guardrails and mandatory disclaimers are required across every UI surface.

## Open items
- **Backlog Features:** Statute-change alerts, interactive letter generator, eviction-response workflow, expanded county coverage, case-law layer, and anonymous mode.
- **Pre-launch Verifications:** Re-verify county court `sourceUrl` and `verifiedOn` records in `src/lib/counties.ts` before public launch.
- **Migration:** Execute planned migration to Stan's own GitHub + Firebase Hosting + Convex account.
