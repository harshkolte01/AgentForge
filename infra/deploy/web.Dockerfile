FROM node:24.21.0-bookworm-slim AS build
WORKDIR /app
RUN npm install --global pnpm@10.34.6
COPY apps/web/package.json apps/web/pnpm-lock.yaml apps/web/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY apps/web ./
# The public origin (for social cards, the sitemap and robots). Public pages are prerendered, so it is read at build time.
ARG SITE_URL=""
ENV NEXT_TELEMETRY_DISABLED=1 API_ORIGIN=http://api:8000 SITE_URL=$SITE_URL
# `output: "standalone"` (next.config.ts) writes .next/standalone: server.js and only the dependencies it traces as needed.
# The /api rewrite to API_ORIGIN is fixed into the build here.
RUN pnpm build

FROM node:24.21.0-bookworm-slim
WORKDIR /app
ARG SITE_URL=""
# server.js listens on HOSTNAME:PORT. SITE_URL also reaches pages rendered per request.
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 API_ORIGIN=http://api:8000 SITE_URL=$SITE_URL HOSTNAME=0.0.0.0 PORT=3000
# Static assets and public/ are not part of the standalone output; server.js serves them from these paths.
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
COPY --from=build --chown=node:node /app/public ./public
USER node
EXPOSE 3000
CMD ["node", "server.js"]
