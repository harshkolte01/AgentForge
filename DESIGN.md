---
name: AgentForge Public
description: Premium public landing and authentication surfaces for a coding agent that keeps the final say with the builder.
colors:
  public-background: "#0b0b0b"
  public-foreground: "#f1f0ee"
  surface-card: "#171615"
  surface-popover: "#1b1a19"
  surface-secondary: "#252321"
  surface-muted: "#242220"
  surface-accent: "#302d29"
  primary: "#eae8e4"
  primary-foreground: "#181716"
  secondary-foreground: "#efedeb"
  muted-foreground: "#aaa7a3"
  accent-foreground: "#f1f0ee"
  border: "#34312e"
  input-border: "#615c56"
  focus-ring: "#c4bdb5"
  brand: "#d5d0c9"
  brand-foreground: "#181716"
  sidebar: "#1a1917"
  sidebar-accent: "#292622"
  destructive: "#ffb4a8"
  selection: "#d3ccc4"
  preview-border: "#3f3b36"
  header-surface: "#141312"
typography:
  display:
    fontFamily: "Manrope, ui-sans-serif, system-ui, sans-serif"
    fontSize: "30px"
    fontWeight: 600
    lineHeight: 1.18
    letterSpacing: "-0.03em"
  section-title:
    fontFamily: "Manrope, ui-sans-serif, system-ui, sans-serif"
    fontSize: "30px"
    fontWeight: 500
    lineHeight: 1.2
    letterSpacing: "-0.025em"
  body:
    fontFamily: "Manrope, ui-sans-serif, system-ui, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.8
  label:
    fontFamily: "Manrope, ui-sans-serif, system-ui, sans-serif"
    fontSize: "12px"
    fontWeight: 500
    lineHeight: 1.5
    letterSpacing: "0.06em"
  code:
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace"
    fontSize: "12.5px"
    fontWeight: 400
    lineHeight: 1.6
rounded:
  micro: "6px"
  sm: "8px"
  md: "10px"
  lg: "14px"
  xl: "18px"
  full: "9999px"
spacing:
  xs: "8px"
  sm: "16px"
  md: "24px"
  lg: "32px"
  section: "80px"
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.primary-foreground}"
    typography: "15px Manrope, 500"
    rounded: "{rounded.sm}"
    padding: "0 16px"
    height: "44px"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.muted-foreground}"
    typography: "14px Manrope, 500"
    rounded: "{rounded.sm}"
    padding: "0 16px"
    height: "44px"
  public-card:
    backgroundColor: "{colors.surface-card}"
    textColor: "{colors.public-foreground}"
    rounded: "{rounded.lg}"
    padding: "24px"
  auth-input:
    backgroundColor: "{colors.public-background}"
    textColor: "{colors.public-foreground}"
    typography: "14px Manrope, 400"
    rounded: "{rounded.lg}"
    padding: "10px 14px"
    height: "44px"
  preview-window:
    backgroundColor: "{colors.surface-card}"
    textColor: "{colors.public-foreground}"
    rounded: "{rounded.lg}"
    padding: "0"
  public-navigation:
    backgroundColor: "{colors.header-surface}"
    textColor: "{colors.muted-foreground}"
    typography: "14px Manrope, 500"
    height: "72px"
    padding: "0 20px"
---

# Design System: AgentForge Public

## Current public-page refinement — 2026-10-11

Latest refinement: section, footer, header, FAQ, and example-list divider lines are removed. Whitespace separates content; FAQs use a quiet open/hover surface. The illustrative workspace follows the actual conversation source: no window title bar or detached changes rail, a sidebar search, inline activity, wrapped approval detail, and the two-row composer. It remains a local demo with no action API calls, keyboard focus restoration after decisions, and an explicit demo label. Desktop/390px/320px browser checks, lint, 606 frontend tests, and the production build passed.

This revision supersedes the older composition and verification notes below. The landing page now contains the hero, one interactive example, a three-step workflow, an editorial section with three example requests, three practical FAQs, a closing action, and a compact footer. The feature grid, control cards, use-case tabs, duplicate sandbox illustration, and their unused components were removed. Public copy describes user outcomes; provider behavior, credential routing, server sessions, setup commands, and implementation guarantees do not belong on these surfaces.

The hero retains the original pixel wordmark and ASCII asset, with quieter background opacity, a 672px copy column, a 48px desktop heading, and a 1152px preview. Navigation contains How it works, What you can do, and FAQ. The closing CTA reuses the existing ASCII artwork at 40% opacity, with top and bottom fades and centered copy; the clear center protects text contrast. The example section uses two editorial columns on desktop and a single column on mobile, without feature cards. Authentication uses a centered 390px form with an inline home/brand link, without a navigation header, background illustration, or footer. GitHub and local-token flows, safe return paths, and retry states remain intact.

Validation: production Webpack build and changed-file ESLint passed; all 606 frontend tests passed. Desktop (1440px) and mobile (390px) screenshots were inspected. Browser checks passed for approval/decline/replay, mobile navigation, FAQ disclosure, both GitHub auth pages, and local/disabled/unavailable/retry auth states, without horizontal overflow or browser errors. Auth state checks used fixtures; live OAuth and deployment are not qualified by these checks. Evidence: `.local/ui-qa/public-polish/` and `.local/public-polish-*.log`.

## Overview

**Creative North Star: "The product is the hero"**

AgentForge's public surfaces use the user-pinned Conductor direction as a quiet frame for the work itself: near-black warm charcoal, off-white type, restrained borders, and a large product preview. The first viewport is intentionally compact. A slim navigation gives way to a centered 520px copy column, a solid light CTA, and an oversized conversation window where the visitor can inspect and approve or decline an illustrative edit.

The atmosphere comes from the generated ASCII ribbon image, which recedes behind the copy instead of becoming a decorative focal point. The original pixel `Wordmark` in `features/landing/wordmark.tsx` carries the public hero identity. The shared `BrandMark` in `components/brand-mark.tsx`, backed by the committed `public/brand/agentforge-icon-128.png`, remains the compact mark for navigation, authentication, footer, and the product preview; these two identity assets have different jobs and should stay distinct.

This document covers `/` in Persuade mode and `/sign-in` plus `/sign-up` in Operate mode. The public visual layer is scoped to `[data-public]`; the authenticated workspace keeps its existing appearance and user preference. Copy and preview states must remain truthful: the product explains research, code, persistent sandbox work, and approval control without invented customers, benchmarks, or live run results.

The generated desktop capture at `.impeccable/review/desktop.png` was inspected as a composition reference. It predates the final source fixes, while retaining the same composition. The finish review was source-scored; post-fix mobile or auth screenshots and live OAuth qualification were not performed.

**Key Characteristics:**

- Warm near-black canvas with soft ivory controls and quiet warm-gray structure.
- Subtle, generated ASCII topology with generous negative space behind short copy.
- Compact hero followed immediately by a large, interactive approval preview.
- Pixel wordmark for the hero and the shared geometric `BrandMark` everywhere the compact mark is needed.
- Tonal layering and 1px borders carry depth; the public surface stays restrained and legible.

## Colors

The public palette is monochrome with a warm cast. Solid ivory is the action voice; the muted stone brand color is reserved for status, live indicators, and small highlights. Green and rose appear only as semantic diff and result colors inside illustrative code output.

### Primary

- **Soft Ivory Control**: The solid primary surface for hero, header, auth, and approval actions. Use dark text on it for clear contrast.
- **Quiet Stone Brand**: A sparse highlight for live dots, icons, approval cues, and the small halo or timeline accents that explain state.

### Secondary

- **Warm Charcoal Secondary**: A lifted control and badge surface for secondary actions, status pills, and compact app-preview chrome.
- **Muted Charcoal**: The low-contrast field behind code, message bubbles, selected tabs, and supporting content.

### Neutral

- **Near-Black Canvas**: The public page and auth shell background. Keep the center of the hero visually quiet.
- **Off-White Foreground**: Primary copy, headings, wordmark, and high-priority app-preview text.
- **Muted Stone Text**: Supporting copy, navigation defaults, preview metadata, and explanatory labels.
- **Warm Border**: 1px separators, card outlines, input strokes, and window chrome.
- **Soft Focus Ring**: Keyboard focus outline with a visible offset from the control.
- **Warm Header Surface**: The slightly lifted sticky header surface when the page is at rest or has been scrolled.
- **Preview Border**: The slightly brighter frame around the oversized product window.
- **Soft Destructive**: Public error messaging and declined or invalid states; it is used sparingly.

**The Public Scope Rule.** These dark values belong inside `[data-public]` wrappers only. Do not move them into the workspace's global appearance tokens or make the workspace inherit the public identity.

**The One Bright Control Rule.** Keep light solid controls rare and purposeful: primary navigation action, hero CTA, auth continuation, and the approval action. Use muted text, borders, and the brand highlight for everything else.

## Typography

**Display Font:** Manrope (with `ui-sans-serif`, `system-ui`, and platform emoji fallbacks)

**Body Font:** Manrope (with the same UI fallbacks)

**Label/Mono Font:** System monospace for tool names, code, commands, and diff content.

Manrope is loaded locally from `public/fonts/manrope.ttf` with its bundled Open Font License text at `public/fonts/OFL-Manrope.txt`; the public shell uses the 200–800 variable range. The pairing is compact, contemporary, and technical without turning the landing page into a terminal. The pixel wordmark is a custom SVG bitmap alphabet and is not a font substitute.

### Hierarchy

- **Display** (600, 30px, 1.18 line-height, -0.03em tracking): The hero title and auth title. At the `sm` breakpoint it becomes 36px while keeping the compact two-line rhythm.
- **Section title** (500, 30px, tight tracking): Public section headings; at `sm` they become 36px. Keep them short and text-balanced.
- **Body** (400, 15px, 1.8 line-height): Hero support copy, auth leads, feature descriptions, and explanatory landing content. Smaller 14px copy is used for compact navigation and dense metadata.
- **Label** (500, 11–13px, approximately 0.06em tracking): Eyebrows, preview badges, tool metadata, and all-caps category labels. Labels should remain quiet rather than compete with headings.
- **Code** (400, 12.5–13px, 1.6 line-height): Commands, tool arguments, file edits, and compact preview output. The right-hand code rail can use 11px text with a looser 24px line-height.

**The Solid Type Rule.** Public copy is rendered in solid foreground or muted colors. Do not use gradient text, glowing type, or colorful headline treatments.

## Layout

The landing page uses a centered `max-w-6xl` content frame (1152px) with 20px horizontal padding that grows to 32px at `sm`. Public sections use 80px vertical padding and grow to 112px at `sm`; the footer and final CTA follow the same broad breathing room. The hero is the one deliberate compression: the copy sits in a 520px column, with 64px top padding and 56px bottom padding on narrow screens, growing to 80px on `sm`.

The first viewport order is fixed: 72px navigation, the pixel wordmark, a two-line headline, a light CTA, two brief support lines, then the full-width product preview. The preview can grow to 1440px. It is the signature visual and should appear oversized enough to feel like the product itself is being handed over to the visitor.

Responsive behavior follows the existing utility breakpoints:

- Below 768px, the section navigation and header actions collapse into an absolute overlay menu. The menu does not enter document flow or move the scroll target; links retain at least 44px of touch height.
- At 640px, the hero wordmark grows from 240px to 280px, the title grows from 30px to 36px, hero copy gains vertical room, and the primary CTA changes from full width to 285px.
- At 768px, the preview's 208px conversation sidebar becomes visible, while its center thread remains the primary reading surface. On narrower screens the sidebar is removed rather than squeezed.
- At 1024px, content grids become two-column layouts for feature/control/how-it-works sections; the code and explanation panes retain their hierarchy.
- At 1280px, the preview's 330px changes rail becomes visible. It is hidden below that width so the approval card and conversation remain readable.
- The hero thread is 448px high on narrow screens and 496px at `sm`; it scrolls internally so the page itself does not jump when a demo step arrives.

Authentication uses the same public shell but a calmer centered composition: an 80px header, 24px horizontal padding growing to 40px at `sm`, a main region with 64px vertical padding, and a 430px outer / 390px content maximum. Its background image is deliberately quieter than the landing hero. The auth panel is a readable surface with no invented marketing ornament.

## Elevation & Depth

Depth is tonal and structural. Public cards use a warm charcoal fill and a 1px border; the product window uses its brighter preview border and a dark charcoal interior. The sticky header gains a translucent background and backdrop blur after scrolling or when its mobile menu is open. The hero image is faded into the canvas at its lower edge, and the how-it-works timeline uses a restrained brand-to-border fade as a structural guide. Heavy drop shadows, glass panels, neon glows, and glossy gradients do not belong to this world.

The only small shadow treatment is state-specific window or menu chrome. The approval message uses a tiny 1px ambient shadow in its light fallback and no meaningful shadow in the dark public state; the mobile menu may use a conventional raised shadow so its boundary is clear.

Motion is short and informative: primary controls transition background and text in 160ms; newly exposed preview rows rise in 280ms with a decelerating cubic-bezier; FAQ plus icons rotate when their native disclosure opens; the demo starts when its frame enters view and keeps its scroll inside the thread. `prefers-reduced-motion: reduce` removes public animation, transitions, and smooth scrolling, and the demo advances without scripted delays or smooth internal scrolling.

## Shapes

The form language is gently rounded and consistent: compact controls use 8px corners, default controls use 10px, cards and auth fields use 14px, message bubbles and larger preview groups use 18px, and pills use fully rounded ends. Borders are 1px and warm rather than stark white. Inputs, buttons, tabs, links, native `details`, and skip links all expose a 2px focus ring with a 4px offset where the shared public rule applies.

The pixel wordmark is a crisp rectangular bitmap silhouette. The shared `BrandMark` is an approved geometric PNG with transparent exterior corners; preserve its proportions and original colors. Do not tint, stretch, rotate, or add effects to either identity asset. The contrast comes from the mark's placement and surrounding negative space.

## Components

### Buttons

- **Character:** Solid, quiet, and decisive. Buttons are rounded rectangles with no ornamental gradients.
- **Primary:** Light ivory fill with dark foreground, 44px minimum height, 16px horizontal padding, Manrope at 15px/500 for the main CTA, and a compact arrow icon at the trailing edge. The hero CTA is full width below `sm` and 285px at `sm`; auth continuation is full width and 48px high.
- **Hover / Focus:** On hover-capable devices the primary surface becomes pure white with near-black text. Keyboard focus uses the public ring and offset. Use the shared 160ms transition; reduced motion removes it.
- **Ghost / Secondary:** Transparent or muted charcoal surfaces carry `Sign in`, `Decline`, `Replay`, and supporting actions. They gain foreground contrast on hover; `Decline` stays visually quieter than `Approve`.

### Chips and Tabs

- **Style:** Segmented tabs use a fully rounded muted container with 4px inner padding. The selected tab is the public background with a 1px ring/border and foreground text; unselected tabs use muted text and gain contrast on hover.
- **State:** Tab lists are keyboard navigable with arrows, Home, and End. Touch pointers receive at least 40px tab height. Preview badges such as `Sample`, `Draft`, and mode labels use small rounded pills with borders and muted text.

### Cards / Containers

- **Corner Style:** Landing cards use 14px corners; larger illustrative preview groups use 18px. Keep one consistent radius per composition.
- **Background:** Use the card charcoal for `lp-card`, the near-black public background for inset surfaces, and the slightly lifted secondary/muted tones for messages and code.
- **Border:** Use the warm 1px border to separate surfaces. The oversized `lp-window` uses the brighter preview border.
- **Internal Padding:** Standard cards use 24px, growing to 32px at `sm`; compact preview chrome uses 12–20px depending on the rail.

### Inputs / Fields

- **Style:** The local bootstrap-token field is full width, 44px high, 14px text, 10px vertical and 14px horizontal padding, a near-black background, warm input stroke, and 14px corners. It is the only public input; there is no password-account flow.
- **Focus:** The field shifts to the soft focus ring and a 2px ring without changing layout. Invalid state uses the soft destructive color and an adjacent alert message.
- **Disabled / Busy:** During token submission the field and action become unavailable with the existing opacity treatment. Unreachable auth state presents a real retry action instead of a decorative error.

### Navigation

- **Landing:** Sticky 72px header with the 32px shared `BrandMark`, compact section links from `md` upward, and `Sign in` plus the primary action. Active section links use a muted pill; default links are muted text. At smaller widths the menu is an overlay with an explicit close action and 44px rows.
- **Authentication:** 80px header with the 32px shared `BrandMark`, the `AgentForge` label, and a `Back to home` link. The footer is centered, small, and muted.

### Approval Preview

The approval preview is the signature component and the reason the product remains the hero. It is a large bordered app window with a compact title bar, conversation thread, sample badge, collapsed native activity/worked disclosure, and an approval card that shows the exact illustrative file edit. The visitor can choose `Approve` or `Decline`, see the truthful follow-up, and `Replay` the script. The labels are synthetic sample UI, not a live run; keep the `Sample` and illustrative framing visible whenever this component is reused.

### Authentication Panel

The auth panel is wired to the existing session handlers. It reads `/auth/session`, uses the real GitHub start route for hosted sign-in, and uses the existing local bootstrap-token session route when the workspace is configured for local access. It handles checking, unreachable/retry, disabled setup, signed-in redirect, GitHub sign-in, and local-token unlock states. Sign-up and sign-in share the same GitHub identity flow, with copy explaining that the first GitHub sign-in creates the account; repository access remains a separate choice. Preserve safe same-origin return paths and the existing error-query handling.

## Do's and Don'ts

### Do

- **Do** keep public styles and colors scoped to `[data-public]`; the authenticated workspace's light/dark/system appearance is a separate product surface.
- **Do** keep `Wordmark` for the hero identity and the shared `BrandMark` for compact brand surfaces, including navigation, auth, footer, loading, and the product preview.
- **Do** load the committed local Manrope font and preserve its Open Font License notice.
- **Do** keep the generated `public/images/ascii-flow.webp` understated, with its provenance JSON beside it; preserve its negative space and use the hero's stronger fade or auth's quieter opacity as the source does.
- **Do** retain the approval preview's exact-action language, native collapsed activity disclosure, synthetic/sample labels, and approve/decline interaction.
- **Do** preserve visible keyboard focus, native FAQ disclosure behavior, safe auth routing, and the reduced-motion behavior described above.
- **Do** label illustrative output as sample or illustrative and keep public claims grounded in current product behavior.

### Don't

- **Don't** add gradient text, neon accents, colorful hero lighting, or a busy dashboard wall that competes with the product preview.
- **Don't** tint, stretch, rotate, or apply effects to the shared `BrandMark` or replace the original pixel wordmark with a generic type treatment.
- **Don't** turn the ASCII asset into readable words, a logo, binary rain, or a high-contrast background; its source prompt calls for quiet monochrome texture.
- **Don't** invent a password form, fake OAuth state, repository permission claim, testimonial, benchmark, or live run result in the public surfaces. Keep the existing GitHub and local-token handlers authoritative.
- **Don't** promote public dark variables into workspace components or change unrelated authenticated workspace styling while extending this system.
- **Don't** treat `.impeccable/review/desktop.png` as a post-fix or cross-device qualification. It is a pre-fix desktop composition reference; mobile, auth visual, and live OAuth visual checks remain unresolved.
