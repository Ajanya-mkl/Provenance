---
name: Provenance
description: Supplier risk console. A calm instrument for trust, built from layered liquid glass over a slowly drifting ambient field.
colors:
  ground: "#07131f"
  ground-2: "#0b1e2e"
  ink: "#eef5f9"
  ink-2: "#bdd0dc"
  ink-3: "#a7becd"
  line: "rgb(190 220 235 / 0.12)"
  accent: "#2ee6b6"
  accent-ink: "#032a22"
  tier-low: "#48e0a0"
  tier-medium: "#ffc766"
  tier-high: "#ffa194"
  danger: "#ffa194"
  glass-solid: "#10283a"
  glass-strong-solid: "#0d2232"
  ground-light: "#e6eff4"
  ground-2-light: "#f3f7fa"
  ink-light: "#0a1c2b"
  ink-2-light: "#2b4558"
  ink-3-light: "#46606f"
  accent-light: "#006b52"
  tier-low-light: "#066a47"
  tier-medium-light: "#874c00"
  tier-high-light: "#b8261f"
  ember-teal: "#1c7293"
  ember-mint: "#02c39a"
  ember-amber: "#f2a541"
  factor-delay: "#3987e5"
  factor-late: "#d95926"
  factor-country: "#199e70"
  factor-delay-light: "#2a78d6"
  factor-late-light: "#eb6834"
  factor-country-light: "#1baf7a"
  series-primary: "#3fa7d6"
  series-muted: "#6f8899"
  series-primary-light: "#1c7293"
  series-muted-light: "#9fb2bf"
typography:
  display:
    fontFamily: "-apple-system, 'SF Pro Display', 'SF Pro Text', 'Inter Variable', Inter, system-ui, sans-serif"
    fontSize: "34px"
    fontWeight: 700
    lineHeight: 1.1
    letterSpacing: "-0.03em"
  numeral:
    fontFamily: "-apple-system, 'SF Pro Display', 'SF Pro Text', 'Inter Variable', Inter, system-ui, sans-serif"
    fontSize: "40px"
    fontWeight: 700
    lineHeight: 1
    letterSpacing: "-0.03em"
    fontFeature: "'tnum' 1"
  headline:
    fontFamily: "-apple-system, 'SF Pro Display', 'SF Pro Text', 'Inter Variable', Inter, system-ui, sans-serif"
    fontSize: "20px"
    fontWeight: 600
    lineHeight: 1.3
    letterSpacing: "-0.02em"
  title:
    fontFamily: "-apple-system, 'SF Pro Display', 'SF Pro Text', 'Inter Variable', Inter, system-ui, sans-serif"
    fontSize: "17px"
    fontWeight: 600
    lineHeight: 1.4
    letterSpacing: "-0.022em"
  reading:
    fontFamily: "-apple-system, 'SF Pro Display', 'SF Pro Text', 'Inter Variable', Inter, system-ui, sans-serif"
    fontSize: "17px"
    fontWeight: 400
    lineHeight: 1.75
  body:
    fontFamily: "-apple-system, 'SF Pro Display', 'SF Pro Text', 'Inter Variable', Inter, system-ui, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "-apple-system, 'SF Pro Display', 'SF Pro Text', 'Inter Variable', Inter, system-ui, sans-serif"
    fontSize: "13px"
    fontWeight: 500
    lineHeight: 1.4
  caption:
    fontFamily: "-apple-system, 'SF Pro Display', 'SF Pro Text', 'Inter Variable', Inter, system-ui, sans-serif"
    fontSize: "12px"
    fontWeight: 400
    lineHeight: 1.5
rounded:
  swatch: "3px"
  chip: "6px"
  inset: "10px"
  sm: "14px"
  md: "20px"
  lg: "28px"
  pill: "999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "24px"
  2xl: "32px"
  3xl: "64px"
components:
  button-pill:
    textColor: "{colors.ink}"
    rounded: "{rounded.pill}"
    padding: "6px 14px"
    typography: "{typography.label}"
  button-pill-accent:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.accent-ink}"
    rounded: "{rounded.pill}"
    padding: "6px 14px"
  panel-glass:
    rounded: "{rounded.md}"
    padding: "24px"
  panel-glass-strong:
    rounded: "{rounded.lg}"
    padding: "28px"
  kpi-tile:
    rounded: "{rounded.md}"
    padding: "20px"
    typography: "{typography.numeral}"
    height: "148px"
  input-field:
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: "10px 16px"
    typography: "{typography.body}"
  nav-item:
    textColor: "{colors.ink-2}"
    rounded: "{rounded.sm}"
    padding: "10px 12px"
  nav-item-active:
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: "10px 12px"
  number-chip:
    textColor: "{colors.ink}"
    rounded: "{rounded.chip}"
    padding: "1px 6px"
  number-chip-ungrounded:
    textColor: "{colors.danger}"
    rounded: "{rounded.chip}"
    padding: "1px 6px"
  tier-badge:
    textColor: "{colors.tier-medium}"
    typography: "{typography.label}"
  source-chip-lit:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.accent-ink}"
    rounded: "{rounded.pill}"
    padding: "4px 12px"
---

# Design System: Provenance

## Overview

**Creative North Star: "The Instrument with Receipts"**

Provenance is a calm instrument for trust. Translucent navy glass floats over a deep ground lit by slow fields of teal, mint and a warm amber ember, and every number the agent writes is a small glass chip that knows which tool produced it. The mood is native-app calm, not admin dashboard: a few large surfaces, generous radii, soft layered depth, and a single bright mint accent reserved for the forward decision.

Dark is the default because the console is driven live on a projector; light mirrors every role. Density is moderate. Each screen carries a title, at most four KPI tiles, and two or three glass panels, never a wall of cards. Evidence sits next to the claim, and ungrounded output is shown struck in red under a verdict banner, never softened. The world rejects the category default: a grey admin grid with a coloured KPI row and a blue primary button.

Glass is a budgeted material. Blur is spent only where a surface floats over the ambient field, and every glass surface has a solid fallback for Solid mode and reduced transparency.

**Key Characteristics:**
- Layered liquid glass with a bright top-left edge, faint opposite edge, inset highlight and layered soft shadows.
- A fixed ambient mesh of four radial colour fields drifting on 48 to 72 second loops behind everything.
- Pill controls with a specular sheen; one mint accent pill for the forward action.
- Tabular numerals everywhere a number is compared or sourced.
- Tier state always carries icon plus word plus colour.
- Number receipts: sourced numbers are glass chips; unsourced numbers are struck in red.

## Colors

A deep navy ground lit by drifting teal, mint and amber, with near-white ink in three steps, one mint accent, and status and data hues kept in strictly separate jobs.

### Primary
- **Signal Mint** (accent, dark #2ee6b6 / light #006b52): the forward decision and the "lit" state. Accent pill fill (Review approvals, Approve, Skip to content), the pending count badge in the rail, the active Solid toggle, the current-version dot on the history timeline, the lit Sources chip, the focus ring, caret and selection tint. Text on it is Deep Kelp (#032a22) in dark and white in light.

### Secondary
- **Ember Teal** (#1c7293), **Ember Mint** (#02c39a), **Ember Amber** (#f2a541): the world's light sources. They appear only as the ambient mesh fields (at 0.16 to 0.55 alpha), the three bars of the brand mark, and the amber tint of the mock-data notice. They are atmosphere, never UI state.

### Tertiary (status and data)
- **Tier Low / Medium / High** (dark #48e0a0 / #ffc766 / #ffa194; light #066a47 / #874c00 / #b8261f): risk tier only, always paired with an icon (shield-check, triangle, octagon) and the word. Coral High doubles as Danger for errors, NOT GROUNDED and rejected audit events.
- **Factor series** (delay variability blue, late rate orange, country risk green; dark and light steps in frontmatter): the score-factor stack and its legend only. This is the only set that passed the dataviz validator all-pairs in both modes.
- **Series primary / muted** (dark #3fa7d6 / #6f8899; light #1c7293 / #9fb2bf): two-series comparisons such as this quarter against last in the market-mix chart.

### Neutral
- **Abyss Navy** (ground #07131f, ground-2 #0b1e2e; light #e6eff4 / #f3f7fa): the page ground on `html` and the ambient layer's base and lower glow.
- **Frost Ink** (ink #eef5f9 / light #0a1c2b): titles, values, primary text.
- **Mist Ink** (ink-2 #bdd0dc / light #2b4558): descriptions, metadata, inactive nav and segment labels.
- **Haze Ink** (ink-3 #a7becd / light #46606f): captions, axis ticks, timestamps, footnotes. The weakest text token; measured on rendered glass interiors at worst 5.73:1 dark and 5.58:1 light.
- **Hairline** (line, rgb(190 220 235 / 0.12) dark, rgb(10 40 60 / 0.12) light): row dividers, table rules, chart gridlines, input strokes.
- **Ink washes**: tinted fills are `color-mix(in srgb, var(--ink) N%, transparent)` at 6% (row hover), 8 to 9% (chip and input fill), 12% (active nav), 22% (toggle track off).

### Named Rules
**The Separate Jobs Rule.** Tier colours encode risk tier, factor colours encode score factors, ember colours are atmosphere. A tier colour never appears in a factor chart and a factor colour never marks a tier.

**The Icon and Word Rule.** Tier is never colour alone. Every tier mark carries its icon and its word, including chart legends.

**The Mint Is a Decision Rule.** Signal Mint fills only the forward action, a pending count, an "on" state, or a lit receipt. It is never decoration or a heading colour.

## Typography

**Display Font:** -apple-system / SF Pro Display (Apple platforms)
**Body Font:** Inter Variable (bundled via @fontsource-variable/inter; carries the face on Windows and Linux)
**Label/Mono Font:** same family; the platform monospace only for batch IDs and code snippets.

**Character:** One neo-grotesque family across the whole console, tightened at display sizes (-0.03em) and set with tabular numerals wherever numbers align or are sourced. Hierarchy comes from size and weight, not from typeface contrast.

### Hierarchy
- **Display** (700, 34px, 1.1, -0.03em): page titles (Overview, a supplier name on Investigation).
- **Numeral** (700, 40px, 1, -0.03em, tabular): KPI tile values and the score ring centre. Units drop to 24px semibold.
- **Headline** (600, 20px, -0.02em): sheet and modal titles, investigation section heads. Chain-integrity verdict on Pipeline runs at 24px bold.
- **Title** (600, 17px): panel titles; the NOT GROUNDED verdict word at 17px bold.
- **Reading** (400, 17px, 1.75, max 65 to 68ch): the agent report and flag summary prose, where number chips sit inline.
- **Body** (400, 15px, 1.5): base text, list primaries (semibold), inputs.
- **Label** (500, 13px): KPI labels, form labels, pills, chip text, legends.
- **Caption** (400, 12px): rail subtitles, supplier keys, footer caveat, health lines.

### Named Rules
**The Tabular Rule.** Every number that is compared, sourced or animated uses tabular numerals.

**The Sentence Case Rule.** Labels, titles and buttons are sentence case. The only uppercase text is the GROUNDED / NOT GROUNDED verdict word.

## Layout

A floating glass rail plus a single content column, centred at max 1600px with 16px outer padding (8px on small screens) and a 24px gap between rail and content (12px small). The rail is 232px wide and sticky at full viewport height minus 32px; below the `lg` breakpoint (1024px) it collapses to a 76px icon rail with labels moved to `title` and screen-reader text, and the pending count becomes a corner badge.

Spacing sits on a 4/8px grid: 4 and 8px inside controls, 12px between list items and icon-text pairs, 16px between filter controls, 20px KPI padding, 24px panel padding and grid gaps, 32px under the page header, 64px before the footer. Overview runs four KPI tiles in one row above a two-thirds chart beside a one-third activity feed; tablet and mobile stack. Reading measure is capped at 65 to 68ch; captions at 80ch. Flag lists are single-column grids that drop columns (quarter, factor) below `md`.

## Elevation & Depth

Depth is a hybrid of real blur and layered soft shadows over an animated colour field. The ambient mesh is the lowest layer (fixed, z-index -1); glass panels float above it with a 1px gradient edge (bright at top-left, faint opposite), an inset top highlight and a three-part shadow; tooltips and dialogs sit highest. Light mode keeps the same structure with white edges and paler shadow.

### Shadow Vocabulary
- **Glass lift** (`box-shadow: inset 0 1px 0 var(--highlight), 0 1px 2px rgb(0 0 0 / 0.08), 0 12px 28px -14px var(--shadow), 0 36px 72px -40px var(--shadow)`): every glass, glass-strong and glass-lite surface.
- **Pill sheen** (`box-shadow: inset 0 1px 0 var(--highlight), 0 6px 16px -10px var(--shadow)`): pill controls.
- **Lit ring** (`box-shadow: 0 0 0 4px color-mix(in srgb, var(--accent) 25%, transparent)`): a Sources chip lit by its number receipt.

### Named Rules
**The Blur Budget Rule.** backdrop-filter is spent only on glass (28px, saturate 170%) and glass-strong (36px, saturate 180%). KPI tiles and nested panels use glass-lite, which keeps the edge and shadow without blur. No screen exceeds about four blurred layers.

**The Top-Layer Rule.** A dialog's own backdrop-filter cannot see the page from the top layer, so dialogs blur through `::backdrop` (18px, saturate 150%, fading in over 380ms) and the glass-strong sheet inside spends no blur.

**The Unprefixed Filter Rule.** Author only the unprefixed `backdrop-filter`. Lightning CSS (Tailwind v4) collapses a hand-written `-webkit-` pair to the prefixed form, which Chrome ignores.

**The Transparent Body Rule.** The ground lives on `html` and the fixed ambient layer; `body` stays transparent or the ambient layer stops painting.

**The Solid Fallback Rule.** Every glass fill has a solid twin. Solid mode (`data-solid`) and `prefers-reduced-transparency` swap to the solid fills, drop backdrop-filter, darken the dialog backdrop and remove the grain overlay.

## Shapes

Soft, continuous corners on a three-step scale: 14px for inner surfaces (nav items, inputs, list rows, tooltips, error notes), 20px for glass panels and KPI tiles, 28px for glass-strong surfaces (rail, sheet, modal). Controls are full pills. Small inset objects use 10px (skeleton bars, code chips, factor bar) and number chips 6px; legend swatches are 3px squares. Borders are never solid strokes on glass: the edge is a masked 1px gradient. Hairline rules divide rows inside a surface; inputs use an inset 1px line shadow instead of a border.

## Components

### Buttons
Calm, tactile pills with a specular sheen.
- **Shape:** full pill (999px).
- **Default pill:** white-gradient sheen fill (14% to 5% white in dark, 90% to 60% in light), pill-sheen shadow, gradient edge, 13px label, 6 to 8px by 14px padding, 16px Lucide icon with 8px gap.
- **Accent pill:** Signal Mint fill, accent-ink text, 600 weight. Reserved for the forward decision.
- **Hover / Active:** fill brightens (accent mixes 12% toward white); press scales to 0.97 on the spring curve (cubic-bezier(0.22, 1, 0.36, 1), 180ms). Disabled drops to 50% opacity.
- **Icon pill:** 36px square pill for sheet close.

### Chips
- **Number receipt:** inline tabular semibold number on a 9% ink wash with inset highlight, 6px radius. Hover or focus rings it in mint and lights its tool in the Sources row; a dark tooltip names the tool.
- **Ungrounded number:** coral on 15% danger wash, 2px line-through.
- **Sources chip:** pill on 8% ink wash, ink-2 text; lit state is mint fill with a 4px mint halo.
- **Status chip:** GROUNDED pill on 15% tier-low wash with check icon.

### Cards / Containers
- **Corner Style:** 20px (glass, glass-lite), 28px (glass-strong).
- **Background:** 160deg navy gradient at 0.54 to 0.62 alpha (dark) or white at 0.56 to 0.70 (light); strong variant slightly denser.
- **Shadow Strategy:** glass lift (see Elevation & Depth).
- **Border:** masked 1px gradient edge, never a solid stroke.
- **Internal Padding:** 24px panels, 20px KPI tiles, 28px modal, 16px rail.
- **Flat variant:** inside another surface, a panel becomes a hairline top rule with 24px top padding.

### Inputs / Fields
- **Style:** 8% ink wash fill, inset 1px hairline, 14px radius, 10px by 16px padding, 15px text, label above at 13px medium ink-2.
- **Focus:** inset stroke thickens to 2px Signal Mint.
- **Error:** 13px coral message below; form-level errors sit in a 14px-radius danger wash block with icon.
- **Search:** a pill-shaped field built on the default pill with a leading search icon.

### Navigation
- **Rail:** glass-strong, 28px radius, brand mark and wordmark, five labelled destinations with 19px icons, 15px medium labels.
- **States:** inactive ink-2; hover 6% ink wash and ink text; active 12% ink wash, ink text and inset highlight.
- **Footer of rail:** mock-data notice (amber 18% wash), theme segmented control, Solid mode switch, warehouse health dot.
- **Mobile/tablet:** 76px icon rail with sr-only labels and a corner count badge.

### Segmented control
A pill track holding pill segments; the active segment inverts to ink fill with ground-coloured text. Arrow keys move the selection.

### KPI tile
glass-lite with a 2px hover lift, 13px label, 40px tabular numeral that counts up, note and optional accent pill pinned to the bottom, min height 148px.

### Score ring and factor stack
A 168px ring with a 12px tier-coloured stroke animating on a spring, tabular score at its centre and the tier badge beneath. The factor stack is one 36px bar split into factor-series segments with 2px gaps; hovering one dims the others to 45% and opens a glass-strong tooltip. Every chart has a "Show data table" disclosure.

### Dialogs
Native `<dialog>`. The side sheet (max 720px) slides 56px in on a spring (stiffness 380, damping 36); the modal (max 520px) rises 16px from 0.97 scale. Both blur the page through `::backdrop`.

## Do's and Don'ts

### Do:
- **Do** put every sourced number in the agent's prose in a number receipt chip, and strike unsourced numbers in coral under the NOT GROUNDED banner.
- **Do** pair every tier colour with its icon (shield-check, triangle, octagon) and its word.
- **Do** keep factor-series colours in factor charts only, and give every chart a data-table disclosure.
- **Do** use glass-lite for KPI tiles and nested panels, and stay within about four blurred layers per screen.
- **Do** blur dialogs through `::backdrop`, and give every new glass fill a solid twin for Solid mode and reduced transparency.
- **Do** use the spring curve (cubic-bezier(0.22, 1, 0.36, 1)) for lift and press, and make every animation static under prefers-reduced-motion.
- **Do** keep text at or above Haze Ink (ink-3) contrast on rendered glass: at least 4.5:1.

### Don't:
- **Don't** put a tier colour and a factor colour on the same chart.
- **Don't** author a `-webkit-backdrop-filter` pair; Lightning CSS keeps only the prefixed form and Chrome drops it.
- **Don't** give `body` a background; it hides the ambient layer.
- **Don't** put backdrop-filter on surfaces inside a `<dialog>` or on nested panels inside glass.
- **Don't** use Signal Mint as decoration, a heading colour or a second accent pill competing with the forward action.
- **Don't** draw solid borders on glass; use the gradient edge or a hairline divider.
- **Don't** add uppercase eyebrow or kicker labels above headings.
- **Don't** revert to a grey admin grid with a coloured KPI row and a blue primary button.
