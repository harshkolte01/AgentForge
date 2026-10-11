# Phase 5 — GitHub identity and approved delivery

> **Delivery section superseded.** The identity and sign-in parts below are current. The *exact approval and delivery*
> design (approval hashes, `external_operations`, `/approvals/{id}/execute`) was removed when the agent runtime was unified
> (commit `556de52`); its tables remain unused. Delivery today — branch, commits, pull request, checks, merge, and recovery
> from a lost answer — is described in [delivery](delivery.md). The safety rules that still hold: the user's own expiring token,
> live permission re-checks before every write, drafts by default, a branch only ever moves forward, nothing is overwritten.

Implemented in code; no live GitHub account, installation, callback or write was exercised. Existing local authentication remains available. The running local API/worker and actual database were not restarted or migrated during this work.

## Identity and public access

`AUTH_MODE=github_app` enables GitHub App user authorization with random, single-use OAuth state, an HttpOnly browser binding, S256 PKCE, verified `/user` identity, and transactional public signup plus a private default project. Login links only the immutable GitHub numeric user ID. `PUBLIC_SIGNUP_ENABLED=false` permits existing users to log in while preventing new identities. A non-empty `GITHUB_ALLOWED_LOGINS` admits only the listed logins, new or existing. Creating the App and the full sign-in contract are in [GitHub App setup](github-app.md).

The backend needs `GITHUB_APP_ID`, `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `GITHUB_OAUTH_CALLBACK_URL`, and `ENCRYPTION_KEY` (or `GITHUB_TOKEN_ENCRYPTION_KEY`). `GITHUB_APP_SLUG` enables the install link. `GITHUB_WEBHOOK_SECRET` verifies webhooks; `ENABLE_GITHUB_WRITES` independently gates delivery. Existing private-key and session-secret environment values remain untouched; this workflow uses user access tokens, never installation tokens to elevate a user's rights.

Production needs HTTPS app/API URLs and a trusted proxy that forwards the actual HTTPS scheme, strips untrusted forwarded headers, and preserves the configured API Host. Browser requests use the same-site API proxy. Public sessions use a Secure, HttpOnly, host-only `__Host-agentforge_session` cookie; only its SHA-256 digest is stored. Server-side idle and absolute expiration, logout revocation, identity checks, and a serialized ten-session cap survive API restarts. Mutations require an allowed Origin. GitHub credential values and PKCE verifiers are encrypted with authenticated, versioned Fernet encryption. The deployment encryption key must remain stable; changing it requires users to authorize again.

Expiring GitHub App user tokens are required. Refresh intent is committed before rotating the refresh token. A lost response, a rejected refresh or a failed store fails closed to fresh login rather than retrying a potentially consumed refresh token; a connection that never reached GitHub releases the fence and reports a retriable error. Concurrent requests wait up to 10s for an in-flight refresh, and a fence older than 60s (a crashed attempt) may be retried.

Global paid-task admission is serialized with a PostgreSQL advisory lock, then a user row lock. Defaults are ten global concurrent tasks, 100 tasks and USD20 reserved exposure per UTC day; public users additionally have two concurrent tasks, 20 tasks/USD5 per day, and USD1 per task. Prior-day active tasks remain in reserved exposure. Waiting for approval uses no compute concurrency slot. Local identities bypass personal public limits, while global provider limits still apply.

## Repository authorization and webhooks

Discovery uses `/user/installations` and `/user/installations/{id}/repositories` with the user's token. Both current App installation permissions and current user repository rights must permit access. Cached grants only narrow discovery; they never authorize access. Reads, private source loading, and worker authority boundaries recheck current rights. Private source uses bounded Git tree/blob reads and blob hash verification, with no credential-bearing archive redirects or host checkout.

Raw webhook bytes are size-limited and checked with constant-time HMAC SHA-256 before JSON parsing. PostgreSQL uniquely stores delivery IDs and rejects replay. Supported events reduce cached grants for installation removal/suspension and revoke credentials plus sessions for revoked user authorization. Webhook payloads never grant access; fresh authenticated GitHub API reads establish authority.

## Exact approval and delivery

The approval preview comes from the final immutable coding snapshots and diff artifact. It requires current-revision tests and both independent qualified reviews, a clean secret check, exact diff digest, and a pinned default-branch SHA. The server chooses `agentforge/task-<task UUID without hyphens>` and freezes repository/installation IDs, base/head, changed paths, title/body, commit message/date, diff artifact, revision, expiry, and requested effects in a canonical SHA-256 action. PostgreSQL triggers prevent changing approval action or operation intent fields. Decisions supply the exact hash and are user-bound, expiring and single-use.

Delivery creates changed blobs and a Git tree with `base_tree`; it preserves existing file modes and verifies every final leaf, including untouched repository files. It creates one commit with the pinned parent, creates a new task branch, and opens a draft PR. There are no ref-update, force-push, merge, branch-delete, workflow-file, or repository-secret mutation operations. Current user/App rights and the approved base are rechecked immediately before visible effects. API confirmation must match repository IDs, head SHA/ref, base SHA/ref, draft state and exact GitHub URL.

`external_operations` records each effect's PREPARED/SENT/CONFIRMED/UNKNOWN state before sending. A durable execution lease prevents concurrent approval execution. Git objects are content-addressed and safe to recreate with identical inputs; unknown branch/PR outcomes reconcile remote state before progressing. If a mutable outcome remains unknown and no matching branch/PR is visible, execution stops for reconciliation rather than issuing another write. This intentionally favors a recoverable partial result over duplicates. A consumed approval returns its stored result without writing again.

API: `/auth/github/start`, `/auth/github/callback`, `/auth/session`, `/github/status`, `/github/installations`, `/github/repositories`, `/github/issues`, `/github/webhook`; task approval list/prepare at `/tasks/{id}/approvals`, hash decision at `/approvals/{id}/decision`, and explicit delivery/reconciliation at `/approvals/{id}/execute`. Approval does not automatically execute delivery.

## Qualification

`uv run python -m unittest discover -s tests` verifies signature/encryption tampering, exact PR fields, OAuth App-token rejection, installation filtering, durable-before-send ordering, ambiguous-outcome reconciliation, chunked/declared request limits and malformed signed webhook shapes.

`uv run python examples/phase5_identity_smoke.py` creates and removes a disposable local PostgreSQL database, migrates through 0007, and tests concurrent session cap/restart/revocation, OAuth state binding/replay, signup/default project idempotency, encrypted credentials, token refresh and uncertain-refresh fencing, immutable owner/hash-bound approvals, moved-base rejection, changed-only Git trees with preserved executable mode, webhook signature/replay handling, and the full delivery lifecycle. A mocked GitHub server accepts a PR then loses its response; a fresh service reconciles the persisted UNKNOWN operation to exactly one branch and one draft PR.

Live HTTPS OAuth/cookie behavior, a real selected-repository App installation, webhook transport, real provider permission revocation and a disposable-repository draft PR remain deployment qualification gates. The local tests do not certify those gates or a public production release.

## Primary guidance reviewed

- [GitHub App user access tokens, PKCE and intersected permissions](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-user-access-token-for-a-github-app)
- [GitHub user installation and repository access endpoints](https://docs.github.com/en/rest/apps/installations)
- [GitHub webhook signature verification](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries)
- [GitHub Git trees and preserving the base tree](https://docs.github.com/en/rest/git/trees)
- [GitHub create-only references](https://docs.github.com/en/rest/git/refs)
- [OWASP server-side session expiration](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)
