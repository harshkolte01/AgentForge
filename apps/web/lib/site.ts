/**
 * The public origin of this deployment, for absolute URLs in social cards, the sitemap and robots. Set `SITE_URL` when building
 * (see `infra/deploy/web.Dockerfile`); in development it falls back to the local server.
 */
const FALLBACK = "http://localhost:3000";

function origin(): string {
  try { return new URL(process.env.SITE_URL || FALLBACK).origin; } catch { return FALLBACK; }
}

export const siteUrl = origin();
