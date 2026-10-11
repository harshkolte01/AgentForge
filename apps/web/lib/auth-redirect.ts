/**
 * Where to land after signing in. Mirrors `safe_next` in `apps/api/src/agentforge/auth/routes.py`: only a same-origin path is
 * ever followed, so a crafted `?next=` can never send someone to another site. The API checks it again on its side.
 */
const AUTH_PAGE = /^\/(sign-in|sign-up)(?:[/?#]|$)/;

export function safeNextPath(value: string | null | undefined): string | null {
  if (!value || value.length > 512 || !value.startsWith("/") || value.startsWith("//") || value.includes("\\")) return null;
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    if (code < 33 || code === 127) return null;
  }
  // Coming back to a sign-in page after signing in would loop.
  if (AUTH_PAGE.test(value)) return null;
  return value;
}

/** `/sign-in` or `/sign-up`, carrying the page to return to when it is a safe one. */
export function authPath(page: "/sign-in" | "/sign-up", next?: string | null, extra: Record<string, string> = {}): string {
  const query = new URLSearchParams();
  const target = safeNextPath(next);
  if (target && target !== "/chat") query.set("next", target);
  for (const [key, value] of Object.entries(extra)) query.set(key, value);
  const text = query.toString();
  return text ? `${page}?${text}` : page;
}

/** The API route that starts GitHub sign-in. It is a full-page navigation, never a client-side route. */
export function githubStartPath(next?: string | null): string {
  const target = safeNextPath(next);
  return target && target !== "/chat" ? `/api/v1/auth/github/start?next=${encodeURIComponent(target)}` : "/api/v1/auth/github/start";
}
