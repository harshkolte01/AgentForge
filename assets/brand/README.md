# AgentForge icon

The mark is a forged, geometric monogram: two ivory forms separated by an open
channel, with an ember-orange segment on a graphite tile. Use the supplied icon
on both light and dark backgrounds. Do not tint, stretch, rotate, or add effects.

`agentforge-master.png` is the original generated artwork with transparent
exterior corners. Distribution assets are derived from this master, rather than
redrawn independently:

- `apps/web/public/brand/agentforge-icon-128.png`: the shared `BrandMark` component
  and the social preview card.
- 192px and 512px variants: browser application-manifest icons.
- `apps/web/app/icon.png`: modern browser icon.
- `apps/web/app/favicon.ico`: embedded 16px, 32px, and 48px representations.
- `apps/web/app/apple-icon.png`: opaque 180px Apple home-screen icon.

Run `node scripts/export-brand.mjs` after intentionally replacing the master.
The exporter uses the Sharp version installed with the web application's Next.js
dependency. All runtime assets are committed to the project; production does not
need access to the image-generation service or its original output directory.

Brand surfaces share `components/brand-mark.tsx`: landing header/footer,
authentication, workspace navigation, loading and connection states, and the
landing-page product preview. The social card uses the same PNG directly because
`next/og` has its own renderer.
