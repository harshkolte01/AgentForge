/** What each `?auth_error=` code from the GitHub sign-in callback means for the person (see `apps/api/.../auth/routes.py`). */
const authErrors: Record<string, string> = {
  denied: "You cancelled GitHub sign-in.",
  expired: "The sign-in link expired. Start sign-in again.",
  signup_closed: "New accounts are not open on this workspace yet.",
  not_allowed: "This GitHub account isn't allowed on this workspace.",
  app_misconfigured: "GitHub sign-in is not available for this workspace. Contact your workspace administrator.",
  unavailable: "GitHub couldn't be reached. Try again in a moment.",
  rate_limited: "Too many sign-in attempts. Wait a minute, then try again.",
};

export function authErrorMessage(code: string): string {
  return authErrors[code] ?? "GitHub sign-in could not be completed. Start sign-in again.";
}

/** Why the person was sent to sign in again, from `?reason=`. */
export const SESSION_EXPIRED_MESSAGE = "Your session has expired. Sign in again to continue.";
