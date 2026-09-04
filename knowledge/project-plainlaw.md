# PlainLaw — Ohio consumer legal help

PlainLaw is a national-brand legal-help product focused on guided small-claims workflows, launching Ohio-only. It is built for individuals navigating small-claims disputes who need reliable, retrieval-first legal guidance without open-ended chat or "robot lawyer" claims. This is one of Stan's projects, currently hosted on the legacy hosting platform, with a planned migration to Stan's own GitHub + Firebase Hosting + Convex account.

## Summary
- **Concept & source of truth:** Specifications documented in `projects/plainlaw/SPEC.md` [2026-08-14].
- Concept locked with Stan: proceed with the recommendations in the spec. [Stan, 2026-08-14]

## Business & pricing
- **Positioning:** Never position PlainLaw as a "robot lawyer" or open-ended legal chatbot. The AI explains supplied law and text; it does not provide legal advice.
- **Retrieval-first architecture:** Displays verbatim Ohio Revised Code quotes with official `codes.ohio.gov` links.
- **Early exclusions:** Multi-state content, family or criminal law, attorney-facing tools, and open-ended chat interfaces.
- **Pre-launch blockers:** Separate LLC formation, E&O insurance, human UPL/disclaimer review, and manual human verification of county court workflows.
- **Pricing tiers:** Free tier, Resolve tier (per-matter pricing as primary monetization), and Pro tier for attorneys (deferred beyond year 1).

## Architecture
- **Tech stack:** React/Vite + Convex web app, currently hosted on the legacy hosting platform; slated for migration to Stan's own GitHub + Firebase Hosting + Convex account.
- **Access & security:** Gated by `workspace_members` [deploy, 2026-08-14]; auth mode is authenticated.
- **App URLs:** Prod at `https://o2pf1hkytgjplgqq` (legacy hosting domain); preview at `https://preview-o2pf1hkytgjplgqq` (legacy hosting domain). [deploy, 2026-08-14]
- **Design system:** Warm paper background, ink-navy primary color, brass accent; Fraunces display font, Public Sans body font, IBM Plex Mono for citations. Disclaimers appear on every surface.
- **Routing & surfaces:**
  - *Public surfaces:* Landing page, `/statutes`, `/statutes/:section`.
  - *Authenticated surfaces:* `/dashboard`, `/matter/:id`, `/decoder`.

## Data model
- **`statutes` table:** Public queries enabled by design; public law serves as the SEO front door.
- **`matters` table:** Stores authenticated user matter workflows.
- **Corpus data:** Covers ORC chapters 1925 (small claims), 5321 (landlord-tenant), 1345 (CSPA), and 2305 (limitations), spanning 189 sections. Durable export stored at `projects/plainlaw/data/orc_corpus.json`. [codes.ohio.gov, 2026-08-14]
- **County data:** Managed in `src/lib/counties.ts`; each entry includes `sourceUrl` and `verifiedOn` metadata. Re-verify before public launch. [official court sites, 2026-08-14]
- **Seeding:** Seed with `statutes:bulkInsert` in ~20-item chunks using `bunx convex run`; reseed prod if the production DB is empty.

## Key logic
- **Matter wizard:** 6-step guided workflow producing a complaint draft, county filing checklist, deadlines with `.ics` export, attorney handoff packet, and cited law.
- **Deadline calculations:** Counterclaim deadline = trial date − 7 days (ORC 1925.02(C)); appeal deadline = 30 days (App.R. 4).
- **Document decoder:** Supports pasted text, file upload, or camera scan. Extraction is client-side using `pdf.js`, `tesseract.js`, and `mammoth`; only the extracted text is sent to the `decodeDocument` backend action.

## Integrations & APIs
- **AI gateway actions:** `convex/ai.ts` actions `explainStatute` and `decodeDocument` use gateway `ai_structured_output` with strict UPL (unauthorized practice of law) guardrails.
- **Corpus sourcing:** Scrapes official Ohio statutory sources (`codes.ohio.gov`).

## Status
- **Deployment & access:** App is accessible under authenticated workspace-member restrictions. [deploy, 2026-08-14]
- **Testing:** E2E test scripts — `scripts/test-plainlaw.ts`, `scripts/test-plainlaw-ai.ts`, `scripts/test-decoder-upload.ts` — were all passing after M1/decoder work. [tests, 2026-08-15]

## Gotchas & lessons
- **Rate limiting on scraping:** Per-section fetches on `codes.ohio.gov` trigger HTTP 429 errors; scrape chapter pages instead of per-section pages.
- **Data cleaning:** Trailing "Last updated…" footers must be stripped from scraped statutory text.
- **Compliance:** Strict UPL guardrails and mandatory disclaimers are required across every UI surface.

## Open items
- **Backlog features:** Statute-change alerts, letter generator, eviction-response workflow, more counties, case-law layer, anonymous mode.
- **Pre-launch verifications:** Re-verify county court `sourceUrl` and `verifiedOn` records in `src/lib/counties.ts` before public launch.
- **Migration:** Execute planned migration off the legacy hosting platform to Stan's own GitHub + Firebase Hosting + Convex account.
