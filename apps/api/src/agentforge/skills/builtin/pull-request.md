---
name: pull-request
description: Prepare and open a pull request from the changes in the workspace: verify the work, write a clear title and description, and publish it. Use when asked to open, create or publish a pull request, or to push changes.
---
# Open a pull request

Pull requests are opened by the backend with create_pull_request, as a draft on a new branch. Never use git push, and never ask for credentials.

## 1. Check there is something to publish
- Run `git status` and `git diff agentforge-base --stat`. If nothing changed, say so and stop.
- Look for changes that do not belong: build output, unintended lockfile churn, .env files, large binaries. Remove them or flag them.

## 2. Verify the work
- Run the project's tests, linter and build for the changed area. Fix failures, or state them honestly in the description.
- Read `git diff agentforge-base` as a reviewer would: leftovers, debug prints, secrets, unrelated edits.

## 3. Write the title
- Imperative, specific, at most 72 characters, for example "Fix race in job lease renewal". Follow the project's own convention if you can see one (conventional commits, ticket prefixes).

## 4. Write the description
- **What and why**: the problem and the approach in two to five bullets.
- **How it was checked**: the exact commands and their results.
- **Notes**: risks, follow-ups, breaking changes, migrations.
- Reference an issue ("Closes #123") only if the person gave you its number.

## 5. Open it
- Call create_pull_request. In Ask me mode the person sees the changed files and approves first. If they decline, ask what to change.

## 6. Report
- Give the pull request URL and a one-line summary, and say that it is a draft on a separate branch, not a change to the default branch.
- If create_pull_request is not available, say that publishing is not enabled here and give the title and description ready to paste.
