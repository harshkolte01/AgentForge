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
| `/` | The public landing page. Anyone can read it; for someone already signed in, its buttons open the workspace |
| `/sign-in`, `/sign-up` | Sign in and sign-up, public. There are no passwords: on a hosted workspace both start the same GitHub sign-in (the first one creates the account), and a local workspace shows the bootstrap-token form. Someone already signed in is sent on to `?next=` or `/chat` |
| `/chat` | A new conversation (empty state and composer) |
| `/chat/<conversation-id>` | An existing conversation. Unknown, malformed or other-account ids show "Conversation not found" instead of a blank page |
| `/chat/<conversation-id>/runs/<task-id>` | Run details for one reply, inside the same sidebar shell. "Back to conversation" returns to the thread |
| `/tasks`, `/tasks/<task-id>` | Standalone task list/detail for work that is not part of a conversation (for example custom-agent tests) |
| `/plugins`, `/plugins?tab=skills` | Plugins and skills, as two tabs of one page drawn inside the same sidebar shell (the sidebar's **Plugins** button opens it) |
| `/settings?section=…` | Settings (Account, Agent, GitHub, Conversations, Appearance, Custom agents). `?section=plugins` and `?section=skills` redirect to `/plugins` and `/plugins?tab=skills` |

> The `/chat/<id>/runs/<task-id>` and `/tasks…` pages listed above no longer exist: a reply's activity is shown inline in
> the thread. What the agent did to a repository is reviewed, published and merged from the conversation's **Changes
> panel** (header chip and the card under the reply) — see [delivery](delivery.md).

`/workspace` and `/workspace?session=<id>` (the original URLs) redirect to `/chat` and
`/chat/<id>`; the GitHub sign-in return and `auth_error` flag use `/chat`. Anyone who is not signed in
and opens `/chat`, `/chat/<id>` or `/settings` is sent to `/sign-in?next=<that page>` (with the
`auth_error` code, or `reason=expired` after a session ends), so they come back to the same place after
signing in; the API's `/chat?auth_error=…` redirects keep working because the workspace forwards them.
Signing out returns to `/`. The shell lives in
`app/chat/layout.tsx`, so moving between conversations or opening run details never reloads
the sidebar, history or in-progress thread. The first message of a new conversation moves the
URL to its `/chat/<id>`. Browser tab titles follow the conversation title. The REST API is
unchanged (`/api/v1/sessions…`, `/api/v1/tasks…`); browser URL constants live in `apps/web/lib/routes.ts`.

## Scrolling

One scroll controller owns the thread (`apps/web/lib/scroll-controller.ts`, rules and tests; `lib/scroll.ts` and
`features/sessions/use-conversation-scroll.ts` are its browser side).

- **Sending a message** brings that message to the top of the view with a small gap, and the reply is written beneath it. The
  room under the latest turn is reserved so this works even when the thread is short. The view starts following the live edge
  only once the reply outgrows that room. Until then the turn rests at the top: a composer that shrinks after the send, a view
  that grows (a phone keyboard closing) or text above that re-wraps does not slide it down. A conversation's first message is
  already at the top, so it is not moved at all.
- **The pin is a state, not a one-off scroll.** It is kept until the view is actually there, so a smooth scroll cut short by
  anything (a focus change, a layout shift) is finished instead of leaving the new message under the composer. Anything that
  moves focus in the thread after a send must use `focus({ preventScroll: true })`.
- **The reader always wins.** Wheel, touch, keys, the scrollbar, selecting text and opening a step pause following and keep the
  reader's place; "Jump to latest" or scrolling back to the end resumes it.
- **Opening a conversation** lands on its latest content, or on the start of the turn that is still running.
- The controller is bound to the mounted scroller through a ref callback. The scroller itself (with the sidebar and, for a first
  message, the composer) stays mounted from one conversation to the next; the controller starts over for each conversation.
- **The saved answer replaces the live one at the same height.** The live run keeps the room of the saved answer's action line
  (copy, time) from the start, so the hand-over never moves the view.

## Conversation titles

A new conversation first shows its opening message as its title. Before the agent starts on that message, the worker
makes one small call to the configured model (`workers/conversation_title.py`) and replaces the title with a short
one for the task, in the language the person wrote in ("Fix flaky login test"). The sidebar, the header and the browser
tab pick it up through the usual `conversation.changed` stream; no client change is involved.

* Only the title the app gave itself is replaced. A title set at creation or by **Rename** is never touched, including a
  rename made while the title is being generated.
* A greeting names nothing, so the first message `hey` keeps its text and the next of the first three messages tries
  again. Later messages never do, so existing conversations keep their titles.
* It is optional end to end. If it is switched off (`CONVERSATION_TITLES_ENABLED=false`), refused, slow (8 s), empty
  or malformed, the first-message title stays and the reply goes ahead unchanged. Stop is honoured first.
* One call per task, capped at 512 output tokens and USD 0.01, recorded in the task's usage ledger like a summary
  update (operation key `title:<task>`). It does not appear as agent activity and is excluded from the context
  status. A worker that restarts mid-call charges the call at its upper bound and never repeats it.
* The model sees only the person's own opening messages (up to three, each shortened to 1,200 characters), never
  attachments or replies, and treats them as text to name rather than instructions.

## Sidebar status

Each conversation in the sidebar shows what is going on in it, so you can start work, move to another conversation and
come back when it needs you. Each state has its own shape as well as colour, a name for screen readers and a tooltip.

| Shown | Meaning |
| --- | --- |
| Braille dot spinner (⣾⣽⣻⢿⡿⣟⣯⣷) | The agent is working (or the next queued message is about to start) |
| Shield with a question mark | A tool call is waiting for your **Approve / Decline** |
| Alert circle | The latest reply failed, or a queued message could not start |
| Blue dot, title in bolder text | A reply finished that you have not seen yet |
| Nothing | Idle, or the latest run was stopped by you |

* The server decides (`SessionView.status`), from the active task, its pending approval and the latest finished turn, so
  every tab and device agrees. Listing conversations answers it in three queries however many there are.
* "Seen" is stored per conversation (`conversations.read_sequence`, migration `0013_conversation_read_state`). Opening a
  conversation in a tab you are looking at sends `POST /api/v1/sessions/{id}/read` with the last message shown. It only
  moves forward, is clamped to the last message, and changes neither the order of the sidebar nor the conversation's
  revision. A reply that arrives while you are looking at that conversation is never flagged; one that arrives while the tab
  is hidden stays flagged until you return.
* A failure stays until seen. A queued message that could not start stays "failed" until you resume or cancel it. Existing
  conversations count as seen when the migration runs.
* The sidebar reads the list again by itself: every 4 s while something is working or waiting for you, every 30 s
  otherwise, immediately when the tab is shown or focused, and never while the tab is hidden or a search is showing.
  A screen reader is told when a conversation you are not looking at finishes, fails or needs approval.

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
details are available beside the corresponding turn. While a turn runs the conversation shows the agent's intentional progress notes
and compact activity rows (never internal reasoning), then the answer as it is written; a quiet "Thinking" line covers the gaps. The
activity stays in place, expanded, when the answer arrives in the open view, and is a collapsed "N actions" summary when history is
loaded. The answer stays outside it, with compact sources and downloadable files beneath. Assistant Markdown is rendered as inert
text/formatting with restricted links; it cannot execute supplied HTML or scripts. See `docs/live-activity.md` for how activity, tool
details and the live-to-saved hand-off work.

## Settings

Settings contains Account, Agent, GitHub, Conversations, Appearance and Custom agents (Plugins and Skills are on their own page, from the sidebar). Account uses
the actual authenticated identity and logout action. Model/limit information
comes from the server configuration; no usage totals or live-provider success is
invented. GitHub retains account connection, installation/access management,
repository access listing and refresh.

Every section is built from the same pieces in `features/settings/settings-ui.tsx`: a header, then groups (a small
heading above a bordered card of rows), at one width, so moving between sections never shifts the page. The section list
carries an icon per section and becomes one scrolling row of pills on a narrow screen. Custom agents are a grid of cards;
opening one shows its instructions, tools and source domains, a test form, version history and, behind a disclosure,
disabling, with "All agents" to go back. Creating or editing a version has a Cancel that returns to where you were.

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
