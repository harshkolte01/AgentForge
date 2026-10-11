---
version: 1
slug: "apps-web-app-page-tsx"
primary_target: "apps/web/app/page.tsx"
related_targets: ["apps/web/features/auth/auth-shell.tsx"]
---

# Public landing and authentication
Mode: Persuade on /; Operate on /sign-in and /sign-up.
Audience: developers and builders; action: enter the workspace with existing authentication.

## Direction contract
THESIS: The user-pinned Conductor reference: the product itself is the hero.
OWN-WORLD: Near black, warm charcoal, off-white. Original pixel wordmark, subtle generated ASCII ribbons, quiet borders and solid white controls.
STORY: A coding agent researches, edits and tests in a persistent workspace; the visitor inspects an illustrative approval then enters AgentForge.
FIRST VIEWPORT: Slim navigation, compact left-aligned copy centered in a 520px column, white CTA, then an oversized interactive product window. The background recedes. Signature interaction is the approval demo.
FORM: User-supplied Conductor screenshot and https://www.conductor.build/ supersede seed df4a479c. Fixed dark identity applies only to public pages; workspace appearance is unchanged.
FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

## 2026-10-11 refinement
Keep only hero/demo, three-step workflow, essential FAQ, final action, and compact footer. Remove backend-facing copy and redundant feature/control/use-case cards. Authentication is a quiet centered form with no navigation header. Preserve auth handlers and public-only theme scope. Desktop and mobile visual and fixture interaction checks passed; live OAuth and tunnel deployment remain separate.

Follow-up: add the requested What you can do editorial section with three illustrative task prompts. Reuse the existing ASCII background for a centered closing CTA. Verified at 1440px, 390px, and 320px; build, lint, and all 606 frontend tests pass.
