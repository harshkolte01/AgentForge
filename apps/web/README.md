# AgentForge AI web

Next.js App Router with TypeScript, Tailwind CSS and shadcn/ui.

## Local development

Requires Node.js 20.9 or newer and pnpm (the tested version is recorded in `package.json`).

```powershell
cd C:\Coding\AgentForge\apps\web
pnpm install
pnpm dev
```

Open [http://localhost:3000](http://localhost:3000).

The home page fetches `/api/v1/health/live` and `/api/v1/health/ready` from the browser. `next.config.ts` rewrites `/api/:path*` to `http://localhost:8000/api/:path*`.

Start the API first:

```powershell
cd C:\Coding\AgentForge\apps\api
uv run uvicorn agentforge.main:app --app-dir src --reload --port 8000
```

Then open [http://localhost:3000](http://localhost:3000). Direct API checks: [http://localhost:8000/api/v1/health/live](http://localhost:8000/api/v1/health/live), [http://localhost:8000/api/v1/health/ready](http://localhost:8000/api/v1/health/ready), [http://localhost:8000/docs](http://localhost:8000/docs).

## Checks

```powershell
pnpm lint
pnpm exec next typegen
pnpm exec tsc --noEmit
pnpm build
```

Add further shadcn/ui components with `pnpm dlx shadcn@latest add <component>`.
