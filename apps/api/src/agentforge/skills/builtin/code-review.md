---
name: code-review
description: Review code changes or a part of a codebase for bugs, security problems and maintainability, and report prioritized findings. Use when asked to review code, a diff, a branch or a pull request, or to check a change before merging.
---
# Code review

Review like a careful senior engineer: find real problems, prove them, and rank them.

## 1. Find what to review
- With the workspace: run `git status`, then `git diff agentforge-base` (everything changed in this conversation) and `git log --oneline -n 20`. If the person named another range, branch or files, review that instead.
- With only a selected repository: read the files or paths they named with read_repo_file and search_repo. For a pull request number, get_pr_status shows its state and checks. Say plainly what you could and could not see.
- Read each changed file well enough to understand its context: callers, callees and tests. Never judge a hunk in isolation.

## 2. What to look for, in priority order
1. **Correctness**: wrong conditions, off-by-one errors, empty or null handling, race conditions, leaked resources, error paths that swallow failures, behaviour that changed without being intended.
2. **Security**: untrusted input reaching a shell, SQL, file path, HTML or URL; missing or misplaced authorization checks; secrets in code or logs; unsafe deserialization; requests to user-supplied addresses.
3. **Compatibility and data**: schema or API changes that break callers, migrations, idempotency, backward compatibility.
4. **Tests**: is the new behaviour tested, including failure paths? If you can, run the existing tests and linters and report the results.
5. **Maintainability**: duplication, unclear names, dead code, comments that no longer match the code. Lowest priority; do not nitpick style a formatter handles.

## 3. Verify before you claim
- Confirm each suspected bug by tracing the code that would run, or by running it. Do not report what you have not traced. When unsure, call it "possible" and say what would confirm it.

## 4. Report
- Start with a one-line verdict (ready / ready after minor fixes / needs changes) and a two-sentence summary.
- List findings by severity: Blocker, Important, Minor. For each give file:line, what is wrong, why it matters (a concrete input that gives a wrong result), and a suggested fix, with a short snippet when that helps.
- Mention what is done well when it is notable. State what you did not review.
- Do not edit files during a review unless asked. If asked to fix, do it after the report.
