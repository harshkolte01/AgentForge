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

The home page shows AgentForge AI and a health status placeholder. It does not call a backend health endpoint yet.

`next.config.ts` proxies `/api/:path*` to `http://localhost:8000/api/:path*`, preserving the path and query string. Browser API calls should use relative `/api/...` URLs. Start the FastAPI service on port 8000 when its routes are implemented.

## Checks

```powershell
pnpm lint
pnpm exec next typegen
pnpm exec tsc --noEmit
pnpm build
```

Add further shadcn/ui components with `pnpm dlx shadcn@latest add <component>`.
