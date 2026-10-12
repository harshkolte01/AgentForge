# AgentForge web

The AgentForge workspace and public pages: Next.js (App Router), TypeScript, Tailwind CSS and shadcn/ui.

Setup, configuration and the full-stack dev runner are in the [root README](../../README.md). To work on the web app alone:

```sh
pnpm install        # Node.js 24 and pnpm 10.34.6 (corepack)
pnpm dev            # http://localhost:3000
```

`next.config.ts` rewrites `/api/*` to `API_ORIGIN` (default `http://localhost:8000`), so start the API first
(`uv run uvicorn agentforge.main:app --app-dir src --reload --port 8000` from `apps/api`).

## Layout

| Path | Contents |
| --- | --- |
| `app/` | Routes: landing, sign-in/up, chat, plugins and settings |
| `features/` | UI by feature (sessions, delivery, plugins, skills, settings, landing, agents, auth) |
| `components/` | Shared components, including the shadcn/ui primitives in `components/ui` |
| `lib/` | API clients, stores and the activity reducer |
| `tests/` | Node test runner suites |

## Checks

```sh
pnpm test:web                                   # all tests
pnpm lint
pnpm exec next typegen && pnpm exec tsc --noEmit
pnpm build
```

Add shadcn/ui components with `pnpm dlx shadcn@latest add <component>`. Plugin logos are regenerated with
`node scripts/plugin-logos.mjs` after the catalog changes.
