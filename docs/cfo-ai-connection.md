# AgentForge → cfo.ai: API connection status

Verified against the current source and running API on 2026-10-12. This document contains no secret keys, tokens, cookies, or database credentials.

## Connection details

| Item | Current value |
| --- | --- |
| Configured public origin | `https://evans-ordering-ooo-reference.trycloudflare.com` |
| API base URL | `https://evans-ordering-ooo-reference.trycloudflare.com/api/v1` |
| Local API base URL | `http://127.0.0.1:8000/api/v1` |
| Local interactive API documentation | `http://127.0.0.1:8000/docs` |
| Local OpenAPI document | `http://127.0.0.1:8000/openapi.json` |
| Current public authentication | GitHub App sign-in followed by an AgentForge session cookie |
| Read-only integration authentication | Not implemented |

The public origin is a temporary Cloudflare tunnel and can change. Its `/api/v1/auth/session` endpoint returned HTTP 200. The local documentation and OpenAPI endpoints returned HTTP 200; the public origin's `/openapi.json` returned HTTP 404. Localhost URLs cannot be used by cfo.ai's hosted service to reach this machine.

For an OpenAPI document whose paths already begin with `/api/v1`, its server URL must be the origin above, without appending `/api/v1` a second time.

## Supported authentication

The configured mode is `github_app`. Users sign in through `GET /api/v1/auth/github/start`; protected endpoints then resolve the `__Host-agentforge_session` cookie. This is an ordinary user session, with access to that user's permitted read and write operations. It is not a scoped integration credential.

There is no incoming API-key authentication or general `Authorization: Bearer` authentication for protected data endpoints. A separate development mode, `local_single_user`, accepts a bootstrap Bearer token only at `POST /api/v1/auth/session`, then uses a local session cookie. That flow is loopback-only and is not suitable for this hosted integration.

The generated OpenAPI currently has no `securitySchemes`: authentication is enforced through request dependencies rather than declared OpenAPI security. Missing OpenAPI security declarations do not mean the data endpoints are public.

## Requested read-only data

| Data | Current availability |
| --- | --- |
| Total users, new users, active users | User and task records exist, but no company-wide count or analytics HTTP endpoint exists. |
| Usage | Owner-scoped task reads and per-task activity summaries exist. Company-wide task counts, model token usage, daily usage, and cost evidence are available through an operator CLI export, not HTTP. |
| Subscriptions | No subscription data model or billing API is implemented. |
| Revenue | No revenue API or billing-provider ingestion is implemented. Internal model/tool cost amounts are expenses or estimates, not revenue. |

Existing reads include `GET /api/v1/tasks` and `GET /api/v1/tasks/{task_id}/activity-summary`. They require the user's session and only cover that user's tasks. Task responses can contain business content; they are not a substitute for an aggregate analytics integration.

An operator can export the existing aggregate usage evidence from the repository root:

```sh
PYTHONPATH=apps/api/src apps/api/.venv/bin/python \
  -m agentforge.operations.business_metrics \
  --days 30 --output /tmp/agentforge-usage-30d.json
```

The export is read-only at the database transaction level, spans all application users, and creates a private file without overwriting an existing file. It includes task counts/statuses, daily task usage, per-model input/output tokens, ledger cost categories, and sandbox evidence with coverage limitations. It does not provide user counts, subscriptions, or revenue. This command was inspected, not executed as part of preparing this guide.

## What is needed before connecting

The current API cannot meet the requested read-only cfo.ai connection. It needs a dedicated aggregate metrics API with server-enforced read-only integration authentication, explicit authorization for company-wide aggregates, and a matching OpenAPI document. Start with user counts and usage; expose subscriptions and revenue only after an authoritative billing source is connected. Represent unavailable billing data as unavailable, rather than reporting zero revenue.

Do not import the full application schema as a read-only integration: it contains write operations, and filtering an OpenAPI file does not restrict the underlying credential. No connection or integration token has been created by this documentation work.

## Source references

- [Application and OpenAPI setup](../apps/api/src/agentforge/main.py)
- [Session authentication](../apps/api/src/agentforge/auth/session.py)
- [Sign-in and bootstrap routes](../apps/api/src/agentforge/auth/routes.py)
- [Task routes](../apps/api/src/agentforge/api/routes_tasks.py)
- [Activity summaries](../apps/api/src/agentforge/api/routes_activity.py)
- [Aggregate usage export](../apps/api/src/agentforge/operations/business_metrics.py)
- [Current data models](../apps/api/src/agentforge/database/models.py)
- [Web API forwarding](../apps/web/next.config.ts)
