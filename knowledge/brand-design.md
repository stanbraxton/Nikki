# Green Collar brand system

## Purpose and scope
The workspace keeps its brand in one file, `brand/DESIGN.md`. Every customer-facing artifact should source its visual and voice values from that file's tokens.

Apply this brand system before producing anything an external audience sees: marketing and product copy, emails, documents, decks, images, videos, landing pages, and UI surfaces. Internal scratch work is exempt. Apply it uniformly everywhere an artifact is produced — brand rules attached to one surface but ignored on others defeat the point.

This applies in every context, not only in dedicated workflows.

## Applying the brand
1. Read `brand/DESIGN.md`. If it is missing, set it up first (see "Setting up the brand" below).
2. Source every value from a token. Colors, typography, rounding, spacing, component styles, tone, product names, taglines, and boilerplate must trace to a `DESIGN.md` token or a computed derivative of one (for example, a tint of a palette color). A value that is not in the file is not yours to invent — add it to `DESIGN.md` and tell Stan, or ask him.
3. Resolve logo and font binaries from `brand/assets/`. Use those exact files, never a look-alike substitute.
4. Validate with the design.md CLI when useful:
   ```bash
   npx @google/design.md lint brand/DESIGN.md
   ```
   A lint `error` is blocking. Warnings are advisories to fix or justify.

The token schema, extension groups, and scaffold template are documented below — read them before writing or editing `DESIGN.md`.

## Setting up the brand (first time, or on request)
When `brand/DESIGN.md` is missing, or Stan wants to define or extend the brand, guide him to a complete spec. Cover every important aspect and collect the binary assets that will be reused. Work in a few focused rounds rather than one long questionnaire: pre-fill from what already exists, ask for the rest in compact grouped batches, and let Stan defer anything non-essential.

1. **Start from what exists.** Pull values from what Stan has shared, prior artifacts, or a brand/site URL or files he points you at. Never invent a value Stan has not given.
2. **Walk the brand with Stan**, capturing each group as `DESIGN.md` tokens. Ask for what is still unknown one group at a time:
   - **Colors**: primary, background/neutral, accent, and the text color that sits on each. Get exact values (hex or the CSS form used).
   - **Typography**: heading and body font families, any display or label font, and base sizes.
   - **Shape and spacing**: corner rounding, and how tight or roomy the spacing should feel.
   - **Components**: recurring ones with defined styles (primary button, card), if the brand has them.
   - **Voice**: tone, person, formality.
   - **Messaging**: product name, tagline, value proposition, the one-paragraph boilerplate, and any phrases the brand avoids.
   - **Licensing**: any usage limits on the logo, fonts, or imagery.
3. **Collect the needed assets.** Ask Stan to share the binary files: the logo (ideally an SVG plus a raster fallback), the brand fonts (`.woff2` preferred), and any fixed imagery. Save each into `brand/assets/` and reference it from the `licensing` group. If Stan cannot provide a font file, record the font name and the fallback to use.
4. **Write the spec.** If `brand/DESIGN.md` does not exist, create it from the scaffold template below. If it already exists (an extend or edit), read it first and apply only additive or changed tokens and new sections, preserving every existing value and asset reference. Never overwrite a populated spec with template defaults. Add only confirmed values: leave any unknown token out entirely rather than writing a placeholder or generic default, since downstream artifacts treat every token as a real brand value. Track what is still missing as `TODO:` notes in the prose sections, not as tokens.
5. **Lint** it (`npx @google/design.md lint brand/DESIGN.md`) and fix errors.
6. **Confirm.** Show Stan the finished spec: the values set, the assets in place, and any remaining `TODO`s. Then use the brand.

Never block the task on completeness. Cover every aspect with Stan, but accept "skip for now" for anything non-essential, record it as a `TODO`, and keep going.

## DESIGN.md format
`brand/DESIGN.md` combines machine-readable design tokens (YAML front matter) with human-readable rationale (markdown prose). Tokens are the normative values. Prose explains why they exist and how to apply them. The format is the Google Labs design.md alpha spec, extended in place with `voice`, `messaging`, and `licensing` token groups that follow the same conventions. Binaries live in `brand/assets/`. There are no companion files.

### Token schema
```yaml
version: alpha
name: <string>
description: <string>            # optional
colors:
  <token>: <CSS color>           # hex, rgb(), oklch(), named
typography:
  <token>:
    fontFamily: <string>
    fontSize: <number+unit>      # px, em, rem
    fontWeight: <number>         # optional
    lineHeight: <number>         # optional
    letterSpacing: <number+unit> # optional
rounded:
  <level>: <number+unit>
spacing:
  <level>: <number+unit | number>
components:
  <name>:                        # e.g. button-primary, button-primary-hover
    backgroundColor: <color | {colors.token}>
    textColor: <color | {colors.token}>
    typography: <{typography.token}>
    rounded: <{rounded.token}>
    padding: <number+unit>
voice:
  tone: <string>                 # e.g. "confident, plainspoken, warm"
  person: <string>               # e.g. "first-person plural (we)"
  formality: <string>            # e.g. "professional-casual"
  reading-level: <string>        # e.g. "grade 8"
messaging:
  product-name: <string>
  tagline: <string>
  value-prop: <string>
  boilerplate: <string>          # standard one-paragraph company description
  banned-phrases: <string>       # phrasing the brand never uses
licensing:
  assets-path: brand/assets
  logo: <usage terms or reference>
  fonts: <license terms for the named fonts>
  attribution: <required text, or "none">
```

Token references use `{path.to.token}`, for example `{colors.primary}`. `voice`, `messaging`, and `licensing` are custom extension keys: the design.md linter leaves unknown top-level keys silent, so they validate cleanly.

### Prose sections
`##` headings, in this order when present: Overview, Colors, Typography, Layout, Elevation and Depth, Shapes, Components, Voice, Messaging, Licensing, Do's and Don'ts. Duplicate headings are an error. Add a section only when its rationale helps a downstream artifact (for example, one on-brand and one off-brand voice example under Voice).

Motion is deliberately out of scope: the alpha spec has no motion tokens. Video and animation work should take colors, typography, and voice from `DESIGN.md` and keep timing and easing in their own styleguides.

### Scaffold template
Every value-bearing token is a normative brand value that downstream artifacts consume as confirmed, so the template ships with them all commented out. Add a token only once you have a confirmed value for it, and never write a placeholder or generic default as a token. The only uncommented entries are fixed conventions (`version`, `licensing.assets-path`). Track what is still missing as `TODO:` notes in the prose sections, which are not consumed as tokens.

```md
---
version: alpha
# Uncomment and fill each token only once its value is confirmed with Stan.
# Leave the rest commented so nothing unconfirmed is treated as the brand.
# name: <workspace or company brand name>
# colors:
#   primary: "#RRGGBB"
#   neutral: "#RRGGBB"      # background
#   accent: "#RRGGBB"
# typography:
#   h1: { fontFamily: <font>, fontSize: 3rem }
#   body-md: { fontFamily: <font>, fontSize: 1rem }
# rounded:
#   sm: <e.g. 4px>
#   md: <e.g. 8px>
# spacing:
#   sm: <e.g. 8px>
#   md: <e.g. 16px>
# voice:
#   tone: <e.g. confident, plainspoken, warm>
#   person: <e.g. first-person plural (we)>
#   formality: <e.g. professional-casual>
# messaging:
#   product-name: <name>
#   tagline: <tagline>
#   value-prop: <one sentence>
#   boilerplate: <one-paragraph company description>
licensing:
  assets-path: brand/assets
#   logo: <usage terms or reference>
#   fonts: <license terms>
---

## Overview

TODO: the brand's personality and the feeling artifacts should evoke.

## Colors

TODO: what each color is for.

## Typography

TODO: which token is used where.

## Voice

TODO: how the brand sounds, with an on-brand and an off-brand example.

## Messaging

TODO: when to use the tagline vs value prop vs boilerplate.

## Do's and Don'ts

TODO: concrete rules, especially anything the brand must never do.
```

## Compatibility with a future Workspace Brand Kit
A future app surface for uploading brand assets and values is a separate work stream. Keep it interoperable by holding these invariants, whether the brand was set up by an agent or populated by such an app:

- `brand/DESIGN.md` is the canonical spec and `brand/assets/` (see `licensing.assets-path`) is the canonical binary location.
- Tokens are additive and namespaced by group. A populated subset is valid, and unknown or extra keys are preserved, not errored.
- Consumers read the brand by reading `DESIGN.md` and resolving `assets/`, never by calling a Brand Kit API.
