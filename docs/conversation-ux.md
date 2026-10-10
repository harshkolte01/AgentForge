# Conversation-first workspace

The workspace sidebar contains new conversation, searchable conversation history,
Settings and the signed-in account. Projects remain an internal ownership boundary;
the interface does not ask users to create or select one. Existing conversation
history and custom agents keep their original owner and internal scope.

## URLs

The address bar is the source of truth for what the workspace shows, so links can be
bookmarked, shared with the same account, opened in a new tab and navigated with the
browser's Back and Forward buttons.

| URL | Shows |
| --- | --- |
| `/chat` | A new conversation (empty state and composer) |
| `/chat/<conversation-id>` | An existing conversation. Unknown, malformed or other-account ids show "Conversation not found" instead of a blank page |
| `/chat/<conversation-id>/runs/<task-id>` | Run details for one reply, inside the same sidebar shell. "Back to conversation" returns to the thread |
| `/tasks`, `/tasks/<task-id>` | Standalone task list/detail for work that is not part of a conversation (for example custom-agent tests) |
| `/settings?section=…` | Settings |

`/workspace` and `/workspace?session=<id>` (the original URLs) redirect to `/chat` and
`/chat/<id>`; the GitHub sign-in return and `auth_error` flag use `/chat`. The shell lives in
`app/chat/layout.tsx`, so moving between conversations or opening run details never reloads
the sidebar, history or in-progress thread. The first message of a new conversation moves the
URL to its `/chat/<id>`. Browser tab titles follow the conversation title. The REST API is
unchanged (`/api/v1/sessions…`, `/api/v1/tasks…`); browser URL constants live in `apps/web/lib/routes.ts`.

## Sending a message

The composer sends a message with `routing: "auto"`, platform-managed bounded
budgets (cost capped at the per-user limit, 300 s, no extra source domains; users
do not configure them) and an optional repository. A "Continue saved changes"
switch appears in the repository menu only when a saved coding change exists. It does not ask the user to choose research/general/coding.
New conversations default to **No repo**. A repository selection belongs to the
conversation; switching conversations restores that conversation's selection.
Clearing it explicitly removes repository context and starts a continuation
boundary. Repository selection supplies context, never approval to publish.

Every message, including a greeting, is answered by a real model call through the
bounded intent router and the shared budget/runtime; there are no canned replies.
Each turn receives the conversation so far (the most recent turns in full text, older
turns as short attributed excerpts, within a fixed size budget) so follow-ups resolve
against what was actually said. Repeated requests with the same idempotency key
return the same receipt. The router must read the owned inputs; its result cannot invent resource
grants, repository identifiers or command profiles. Unclear intent produces a
conversational clarification rather than an unsupported completion claim.

Research/general work retains its evidence verification. Coding requires an
explicit selected repository, current access and a qualified E2B environment;
supported test/build profiles are inferred from the actual pinned repository.
Returning to coding after an intervening research turn can reuse the compatible
prior patch. Changing repository or choosing No repo breaks that compatibility.
Publishing still requires separate approval of the exact changes.

Messages remain the primary content. Actual task status, cancellation and run
details are available beside the corresponding turn. Run internals stay out of the thread: while a turn runs the conversation shows only a quiet
"Thinking" indicator, and the finished reply is plain text. Assistant Markdown is rendered as inert text/formatting
with restricted links; it cannot execute supplied HTML or scripts.

## Settings

Settings contains Account, Model & limits, GitHub and Custom agents. Account uses
the actual authenticated identity and logout action. Model/limit information
comes from the server configuration; no usage totals or live-provider success is
invented. GitHub retains account connection, installation/access management,
repository access listing and refresh.

Custom agents are listed across the owner's existing internal scopes, with
bounded pagination. New agents automatically use the default workspace. Editing
an existing agent preserves its original scope, immutable version history and
exact saved-version test behavior. Disable remains soft. Tests are explicit paid
task actions with the existing USD0.25/180-second bounds, not automatic page loads.
`/connections` and `/integrations` redirect to GitHub Settings; `/agents` and
`/agents/new` redirect to agent Settings, retaining supported agent/create links.

## Migration and verification

Revision `0008_automatic_conversation` adds conversation repository selection and
continuation boundaries, permits task-free message/turn receipts, backfills
repository context from legacy coding turns, and allows pinned repository research
sources. It preserves immutable historical messages. A downgrade cannot erase
task-free receipts to satisfy the previous NOT NULL columns; use a reviewed
backup/restore strategy if a rollback is required.

The Settings disposable PostgreSQL fixture passed automatic create, owner-wide
list/pagination, foreign-owner denial, original-scope version updates, exact older
version test queuing and disable. Existing session/research/coding fixtures now
include automatic routing and continuation boundaries. The release runner
requires the Settings fixture alongside them.

The final suite passed 84 unit tests. The frozen release runner passed its
79-test snapshot and all six disposable database workflows; three later bounded
repository-output checks and two migration-filter checks also passed. The
operational fixture additionally proved that exact framework-table exclusions
leave unknown application-schema drift visible.

Frontend lint, TypeScript and an isolated production build passed. Mocked-API Chrome
checks passed desktop/mobile Settings layouts with no horizontal overflow or page
errors, plus version save, version-specific testing, disable, default creation,
legacy redirects, GitHub refresh and logout. These are browser contract checks,
not live GitHub/provider qualification.

The authorized local handoff backed up the serving database and matching private
artifacts, applied revision 0008, and restarted the idle API/worker while preserving
the running Next.js process. Existing message/task/job counts were preserved.
Actual authenticated HTTP checks confirmed a short `hey` reply with `task: null`,
idempotent replay and unchanged task/job/run/budget-operation counts. The canary
conversation was archived. Application metadata checks passed while preserving
the separately managed LangGraph checkpoint tables. No live semantic model task,
GitHub App action or paid provider request was used for this handoff.
