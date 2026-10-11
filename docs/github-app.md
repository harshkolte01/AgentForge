# GitHub App setup

AgentForge signs people in with a **GitHub App** (not an OAuth App) and reads and
writes repositories with each person's own expiring user token, limited to what
both they and the App installation may do. No installation private key is used.

Below, `APP` is your `APP_BASE_URL` (the web app, e.g. `https://agentforge.example`
or `http://localhost:3000`) and `API` is your `API_PUBLIC_BASE_URL` (the same origin
in a deployment; `http://localhost:8000` in development).

## Create the App

GitHub → Settings → Developer settings → GitHub Apps → **New GitHub App**
(or the organization's Developer settings, to own it there).

| Field | Value |
| --- | --- |
| GitHub App name | Anything; its URL slug becomes `GITHUB_APP_SLUG` |
| Homepage URL | `APP` |
| Callback URL | `API/api/v1/auth/github/callback` (must equal `GITHUB_OAUTH_CALLBACK_URL` when that is set) |
| Expire user authorization tokens | **On** (required: sign-in refuses non-expiring tokens) |
| Request user authorization (OAuth) during installation | **Off** (see below) |
| Enable Device Flow | Off |
| Setup URL | `APP/settings?section=github&installed=1` |
| Redirect on update | On |
| Webhook → Active | On, with URL `API/api/v1/github/webhook` |
| Webhook secret | 32+ random characters, also set as `GITHUB_WEBHOOK_SECRET` |

**Repository permissions:** Contents *Read and write*, Pull requests *Read and
write*, Issues *Read-only*, Metadata *Read-only* (mandatory), and — to show CI status
on a pull request — **Checks** *Read-only* and **Commit statuses** *Read-only*. No
organization or account permissions are needed. Without the last two everything works,
but the Changes panel shows "Checks unavailable" instead of the checks. After adding
permissions to an existing App, each installation must accept the update (GitHub emails
the owner; Settings → GitHub shows the repository as needing attention until then).

**Events:** `installation`, `installation_repositories` and `github_app_authorization`
are sent to every App with an active webhook; AgentForge uses them only to drop access
(uninstalls, removed repositories, revoked authorizations), never to grant it. To make
the Changes panel follow a pull request live, also subscribe to **Pull request**, **Pull
request review**, **Check suite**, **Check run** and **Status**. These only mark the
stored status stale so it is read again at once; nothing in their payloads is trusted
beyond choosing which conversation to refresh. Without them the panel polls (every 15 s
while a pull request is open and visible).

Merging, closing, marking ready and pushing are only ever done by the signed-in person
from the Changes panel (and `create_pull_request`, by the agent, never merging); they
need "Contents" and "Pull requests" write access and `ENABLE_GITHUB_WRITES=true`. See
[delivery](delivery.md).

**Where can this GitHub App be installed:** "Only on this account" for a private
deployment, "Any account" if other people will install it on their own accounts.

After creating it, copy the **App ID** and **Client ID**, and generate a **client
secret**. You do not need to generate a private key.

If "Request user authorization during installation" is on, GitHub sends people to
the callback URL (not the Setup URL) after installing, carrying a code that
AgentForge did not request. That code is never used to sign anyone in: a signed-in
person goes to `APP/settings?section=github&installed=1`, anyone else starts a
normal sign-in and lands there afterwards.

## Configure AgentForge

In `.env` (development) or `runtime.env` (deployment):

```sh
AUTH_MODE=github_app
GITHUB_APP_ID=123456
GITHUB_APP_SLUG=your-app-slug
GITHUB_CLIENT_ID=Iv23...
GITHUB_CLIENT_SECRET=...
GITHUB_TOKEN_ENCRYPTION_KEY=...   # 32+ random characters; changing it disconnects everyone from GitHub
GITHUB_WEBHOOK_SECRET=...         # the App's webhook secret
GITHUB_ALLOWED_LOGINS=alice,bob   # who may sign in; see below
PUBLIC_SIGNUP_ENABLED=false
```

The API refuses to start with `AUTH_MODE=github_app` and a missing App ID, client
ID, client secret or encryption key; the message names the settings, never their
values. `python -m agentforge.doctor` (from `apps/api`) checks the same.

**Who can sign in.** A non-empty `GITHUB_ALLOWED_LOGINS` (comma-separated,
case-insensitive) admits exactly those GitHub logins, creating their account on
first sign-in; removing a login stops that person's next sign-in. With no
allowlist, existing accounts can sign in and new ones are created only while
`PUBLIC_SIGNUP_ENABLED=true` (the development default). AgentForge has no usage
limits, so a deployment should always set the allowlist.

**Sessions.** A sign-in lasts `SESSION_ABSOLUTE_TTL_SEC` (default 7 days) or until
it is unused for `SESSION_IDLE_TTL_SEC` (default 24 hours). GitHub user tokens last
8 hours and are refreshed silently; if GitHub rejects a refresh the person is asked
to reconnect.

**Development.** GitHub sign-in works on `http://localhost` (the session cookie is
`Secure`, which browsers accept on localhost). GitHub cannot reach a localhost
webhook; leave the webhook inactive or use a tunnel. Local single-user sign-in
(`AUTH_MODE=local_single_user`) needs no App but reaches public repositories only.

## What the web app can rely on

Sign-in starts at `GET /api/v1/auth/github/start` (optional `next`: a same-origin
path such as `/settings?section=github` to land on afterwards). Success lands on
`APP/chat` (or `next`); a failure lands on `APP/chat?auth_error=<code>` (the web app forwards that to
`/sign-in?auth_error=<code>`, where the message is shown):

| Code | Meaning |
| --- | --- |
| `denied` | The person pressed Cancel on GitHub |
| `expired` | The sign-in link was stale, reused, or opened in another browser; start again |
| `signup_closed` | New accounts are closed (`PUBLIC_SIGNUP_ENABLED=false`, no allowlist) |
| `not_allowed` | The GitHub login is not in `GITHUB_ALLOWED_LOGINS` |
| `app_misconfigured` | Operator error: an OAuth App instead of a GitHub App, token expiry off, wrong client secret or callback URL, or sign-in not configured |
| `rate_limited` | Too many sign-ins right now |
| `unavailable` | GitHub could not be reached, or another failure (logged server-side without secrets) |

`/start` returns these as redirects to a browser navigation and as JSON errors
(`429`, `503`) to a script.

`GET /api/v1/github/status` returns `configured`, `connected` (a usable GitHub
credential), `reauthorization_required` (a credential exists but GitHub needs a
new sign-in), `installations` (how many installations of this App the person can
use; `null` when unknown or not connected), `enabled_writes` and `install_url`.
