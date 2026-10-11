# Delivery: from the agent's changes to a merged pull request

A **delivery** is the GitHub side of one conversation's repository work: the branch AgentForge publishes to, the
commits it has pushed, and the pull request (with its checks and reviews). It is owned by the backend and driven by
the person from the Changes panel; the agent's `create_pull_request` tool uses the same service.

The sandbox holds no credentials. Every GitHub write is made by the API with the signed-in person's expiring user
token, re-checked live (`AuthorizedGitHubClient.authorize(write=True)`), fast-forward only, never forced.
Merge, close and mark-ready are **human-only**: they exist only as the endpoints below, never as agent tools.

## Lifecycle

```
none ──push──▶ branch ──open PR──▶ draft / open ──merge──▶ merged
                  ▲                    │   └────close────▶ closed
                  └──── push more ◀────┘
```

* Changes are the sandbox working tree compared with the last published state (`agentforge-pushed` tag; before the
  first push, `agentforge-base`, the pinned commit the repository was loaded at).
* A push commits those changes on top of the delivery's `head_sha` (first push: creates the branch at `base_sha`).
* Pushing and discarding are refused while a turn is running (`agent_running`), so the diff cannot race the agent.
* After merge or close, **reset** starts a fresh delivery from the updated base branch (the sandbox is reloaded).

## HTTP API

All routes are owner-scoped under `/api/v1/sessions/{session_id}/delivery`, return `Cache-Control: no-store`, and
use `HTTPException` with `detail` = a machine code (table below). Mutations take an `idempotency_key` (UUID):
repeating a key returns the stored outcome instead of acting twice.

| Method + path | Body | Returns |
| --- | --- | --- |
| `GET /delivery` | — | `DeliveryView` |
| `GET /delivery/changes` | — | `ChangesView` |
| `POST /delivery/suggest` | `{}` | `Suggestion` |
| `POST /delivery/push` | `{idempotency_key, message, branch?}` | `DeliveryView` |
| `POST /delivery/pull-request` | `{idempotency_key, title, body, draft=true, message?, branch?}` | `DeliveryView` (pushes pending changes first, using `message`; opens nothing twice) |
| `POST /delivery/ready` | `{idempotency_key}` | `DeliveryView` |
| `POST /delivery/merge` | `{idempotency_key, method: "squash"\|"merge"\|"rebase", expected_head_sha}` | `DeliveryView` |
| `POST /delivery/close` | `{idempotency_key}` | `DeliveryView` |
| `POST /delivery/update-branch` | `{idempotency_key}` | `DeliveryView` |
| `POST /delivery/discard-file` | `{path}` | `ChangesView` |
| `POST /delivery/refresh` | `{}` | `DeliveryView` (re-reads GitHub now) |
| `POST /delivery/reset` | `{idempotency_key, discard_unpublished=false}` | `DeliveryView` (only when the PR is merged or closed, or nothing was published; `delivery_unpublished_changes` 409 if there are unpushed changes and `discard_unpublished` is false) |

### `DeliveryView`

```ts
type DeliveryView = {
  available: boolean;                       // false → show `unavailable_reason`, hide actions
  unavailable_reason: null | "no_repository" | "sandbox_not_configured" | "github_not_connected";
  capabilities: {
    writes_enabled: boolean;                // ENABLE_GITHUB_WRITES
    can_write: boolean | null;              // push access to this repository (null = not yet checked)
    checks_readable: boolean | null;        // App has Checks/Statuses read (null = unknown)
  };
  busy: boolean;                            // a turn is running or queued: push/discard disabled
  repo_id: string | null;                   // "owner/name"
  base_branch: string | null;
  base_sha: string | null;
  branch: string | null;                    // null until the first push
  branch_url: string | null;
  head_sha: string | null;                  // last commit AgentForge pushed
  state: "none" | "branch" | "pull_request";
  pull_request: null | {
    number: number; url: string; title: string;
    state: "draft" | "open" | "merged" | "closed";
    head_sha: string;
    mergeable: "mergeable" | "conflicting" | "unknown";
    behind_base: number | null;             // commits the base has that the branch lacks
    checks: {
      state: "none" | "pending" | "passing" | "failing" | "unavailable";
      passed: number; failed: number; pending: number;
      items: { name: string; status: "pending" | "passed" | "failed" | "neutral"; url: string | null; workflow: string | null }[];
    };
    reviews: { decision: "none" | "approved" | "changes_requested" | "review_required";
               items: { login: string; state: "approved" | "changes_requested" | "commented" | "dismissed" | "pending" }[] };
    merged_at: string | null;
    merge_methods: ("squash" | "merge" | "rebase")[];   // as allowed by the repository
  };
  unpublished: null | { files: number; additions: number; deletions: number; updated_at: string };   // last known summary of
                                            // changes not yet pushed (refreshed when a turn ends and by GET /delivery/changes);
                                            // null = none known. This is what the card and header chip show without opening the sandbox.
  commits: { sha: string; message: string; files: number; additions: number; deletions: number; created_at: string; url: string }[];
  pending_operation: null | { kind: string; started_at: string };   // a write whose outcome is being confirmed
  synced_at: string | null;                 // when GitHub was last read
  sync_error: null | "github_unavailable" | "github_rate_limited" | "github_reauthorization_required" | "github_access_denied";
  version: number;
};
```

`sync_error` set means the last read failed: the panel keeps the previous values and shows **"PR status unavailable ·
Retry"**; it never claims "No PR". `checks.state = "unavailable"` means the App lacks the Checks permission.

### `ChangesView`

```ts
type ChangesView = {
  workspace: "ready" | "expired" | "unavailable" | "no_repository";   // expired: the sandbox is gone; unpublished edits are lost
  baseline: "base" | "published";
  files: { path: string; status: "added" | "modified" | "deleted"; additions: number; deletions: number;
           binary: boolean; truncated: boolean; patch: string;          // `patch` is a unified diff for that file ("" if binary or too large)
           blocked: null | "secret" | "path" | "submodule" | "symlink" }[];   // blocked files cannot be published: the push is refused
                                                                        // (`delivery_blocked_paths:<paths>`) until they are discarded
  additions: number; deletions: number;
  truncated: boolean;                       // more files than shown
  diff_version: string;                     // changes when the working tree does
};
```

### `Suggestion`

`{ commit_message: string; pr_title: string; pr_body: string; branch: string }` — assembled from the conversation's title,
the agent's last answer and the changed files (no extra model call). `branch` is the name the first push will use if sent
back as `branch`; once a branch exists it is ignored. The person can edit everything in the publish dialog.

### Error codes (`detail`)

`agent_running` 409 · `nothing_to_publish` 409 · `delivery_workspace_expired` 409 · `delivery_no_repository` 409 ·
`github_branch_moved` 409 (the branch changed on GitHub) · `github_path_conflict` 409 (files changed on GitHub since the last push;
`detail` is `github_path_conflict:<path>,<path>`) · `github_base_branch_changed` 409 · `github_publish_branch_exists` 409 ·
`github_not_mergeable` 409 · `github_draft_pull_request` 409 · `github_write_permission_required` 403 ·
`github_writes_disabled` 403 · `github_reauthorization_required` 401 · `github_rate_limited` 503 ·
`github_unavailable` 503 · `delivery_blocked_paths:<path>,<path>` 409 (secret, symlink, submodule or unsafe files in the changes) ·
`github_branch_missing` 409 (the branch was deleted on GitHub; its earlier commits are not restored) ·
`github_change_too_large` 409 (over 500 files or 20 MB) · `delivery_not_published` 409 (no pull request to act on) ·
`github_not_connected` 403 · `delivery_sandbox_not_configured` 409 · `delivery_message_required` 422 · `delivery_branch_invalid` 422 · `delivery_workspace_unavailable` 503 (the sandbox did not answer) ·
`github_unsupported_change` / `github_unsafe_change_path` 409 · `delivery_unpublished_changes` 409 · `delivery_reset_not_allowed` 409 · `delivery_workspace_changed` 409 (the files changed while publishing; try again) · `delivery_operation_in_progress` 409 · `delivery_outcome_unknown` 502 (a write may or may not have
happened; refresh).

## Live updates

A delivery change bumps the conversation's revision (`delivery.changed`), so the existing conversation event stream
and session reads refresh it. The panel also polls `GET /delivery`: every 15 s while a PR is open and the tab is
visible, every 60 s otherwise, and on focus. The GitHub webhook (`pull_request`, `pull_request_review`,
`check_suite`, `check_run`, `status`) updates the stored state ahead of the poll.

## Headline (what the person sees, in precedence order)

| Condition | Label | Tone | Primary action |
| --- | --- | --- | --- |
| PR merged | Merged | purple | Continue (reset) |
| PR closed | Closed | muted | Continue (reset) |
| unpublished changes, no PR | `N files changed` | yellow | Create pull request |
| unpublished changes, PR | `N unpushed files` | yellow | Push update |
| mergeable = conflicting | Merge conflicts | red | Resolve (asks the agent) |
| checks failing | Checks failed | red | Fix checks (asks the agent) |
| checks pending | `N checks pending…` | yellow | — |
| PR draft | Draft | muted | Ready for review |
| reviews = changes_requested | Changes requested | red | Address feedback (asks the agent) |
| behind base | `N commits behind <base>` | yellow | Update branch |
| otherwise | Ready to merge | green | Merge (5 s undo) |
| nothing published, no changes | No changes yet | muted | — |

## Safety and recovery

* **No credentials in the sandbox.** Git inside it only measures (`git add -A` into a private index, so the agent's own
  staging and `index.lock` are never touched) and records what was published; every GitHub request is made by the API.
* **Forward only.** A push is one new commit on the branch's *current* head; the ref is updated with `force: false`. If
  someone pushes in between, GitHub refuses and the person is told the branch moved.
* **Nothing of anyone else's is overwritten.** Before a later push, every file it changes must still be on the branch exactly as
  AgentForge last published it (or already hold the new content). Otherwise nothing is written and the paths are named
  (`github_path_conflict:<paths>`). This also covers *Update branch* and teammates committing to the branch.
* **What is sent is what was reviewed.** Each file's bytes are read from the sandbox and checked against the git blob that was
  measured; a file that changed meanwhile fails the push (`delivery_workspace_changed`) instead of publishing something unseen.
* **Secrets stay out.** Secret-looking files (`.env`, keys), symlinks, submodules and `.agentforge/` block the push and are named;
  discard them to continue. Limits: 500 files, 20 MB.
* **A lost answer never duplicates.** Each commit carries an `AgentForge-Operation: <key>` trailer and a pull request is looked up
  by its head branch before one is opened. The same request repeated after a timeout finds what already exists
  (`pending_operation` is shown meanwhile and cleared once confirmed); a different request is refused with
  `delivery_operation_in_progress` for up to 150 s.
* **Authorization is live.** Every mutation re-checks the person's current repository and installation permissions
  (`authorize(write=True)`); cached grants only describe what the panel may offer.
* **Merge, close, ready and update-branch are human-only** (no agent tool). Merge sends the head SHA the person saw, so a pull
  request that moved is refused; the browser holds the click for 5 seconds so it can be undone.
* **Reset** (after a merge or close) archives the delivery and drops the sandbox, so the next message loads the repository again
  from the updated branch. It refuses while a PR is open, and while there are unpushed changes unless the person agrees.

## The agent's part

`create_pull_request` goes through the same service: it commits the workspace changes to the conversation's branch (created on
first use) and opens a draft pull request once; calling it again after more work adds a commit to the same branch and pull
request. `get_pr_status` (new) returns the pull request's state, mergeability, reviews and each failing check with the reason it
gave, so "fix the failing checks" works across turns. Each turn the agent is told, in its environment block, where the work stands
(branch, pull request and checks, changes not yet pushed) and that it cannot merge. The tool still asks for approval in *Ask me*
mode; the panel's buttons are the person's own actions and do not.

## Where it lives

| Part | Code |
| --- | --- |
| Tables `conversation_deliveries`, `delivery_commits` | `database/delivery.py`, migration `0017_deliveries` |
| GitHub operations and status normalisation | `integrations/github_delivery.py` |
| Sandbox git (measure, advance the published marker, discard) | `delivery/workspace.py`; `SandboxProvider.attach` in `integrations/e2b_sandbox.py` |
| Workflow, idempotency, sync, webhook invalidation | `delivery/service.py` |
| HTTP | `api/routes_delivery.py`; webhook events in `api/routes_github.py` |
| Agent tool, `get_pr_status`, turn-end summary | `tools/sandbox_shell.py`, `tools/repository_research.py`, `bootstrap.py` (`after_turn`), `subagents/environment.py` |

Tests: `tests/test_delivery_github.py` (a stateful fake GitHub), `test_delivery_workspace.py` (real git), `test_delivery_routes.py`,
and `test_delivery_service.py`, which needs a disposable migrated database in `AGENTFORGE_TEST_DATABASE_URL` and is skipped without one.

## GitHub App requirements

Contents *Read and write*, Pull requests *Read and write*, Metadata *Read-only* (as before), plus **Checks: Read** and
**Commit statuses: Read** for CI status. Subscribe to **Pull request**, **Pull request review**, **Check suite**, **Check run**
and **Status** events for live updates. Without the extra permissions the panel shows "Checks unavailable" and works
otherwise; without the events it relies on polling.
