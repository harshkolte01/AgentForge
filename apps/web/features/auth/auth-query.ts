import { safeNextPath } from "@/lib/auth-redirect";

export type AuthQuery = {
  /** A same-origin path to return to after signing in, or null. */
  next: string | null;
  /** The callback's `?auth_error=` code (the panel turns it into a sentence). */
  authError: string | null;
  /** Why the person is here again, when it is worth saying. */
  reason: "expired" | null;
};

const first = (value: string | string[] | undefined): string | undefined => (Array.isArray(value) ? value[0] : value);

/** Reads a sign-in page's query defensively: it is attacker-controlled text. */
export function authQuery(query: Record<string, string | string[] | undefined>): AuthQuery {
  const code = first(query.auth_error)?.slice(0, 64);
  return {
    next: safeNextPath(first(query.next)),
    authError: code ? code : null,
    reason: first(query.reason) === "expired" ? "expired" : null,
  };
}
