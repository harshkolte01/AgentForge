---
name: debug-and-fix
description: Diagnose and fix a bug, failing test, crash or error message in code. Use when something is broken, a test fails, or the person pastes an error or stack trace.
---
# Debug and fix

Work like an investigator: evidence first, then a fix.

## 1. Understand the failure
- Establish what was expected, what happened, and since when. Read the whole error or stack trace and find the failing entry point.

## 2. Reproduce it
- Run the failing command or test yourself and keep the exact output. If you cannot reproduce it, say what differs (environment, version, data) and narrow that down.

## 3. Locate the cause
- Start at the innermost stack frame in the project's own code and read it, along with its callers. Use grep and find to follow names.
- Form one to three hypotheses and test each with a cheap check (a print, a small script, a targeted test run) instead of guessing.
- For "it used to work", look at `git log` and `git diff` for recent changes.

## 4. Fix the root cause
- Ask why that state was possible, and fix it there. Do not hide errors with a broad try/except, special-case the failing input, or delete or loosen tests.
- Make the smallest change that fixes it, in the project's style. Leave unrelated code alone.
- Add or update a test that fails before the fix and passes after it, when the project has tests.

## 5. Verify
- Re-run the command that failed, then the surrounding tests, linter and type-check. Report what actually happened. If something still fails, say so.

## 6. Report
- Give the root cause in plain words, the files you changed, how you verified it (commands and results), and anything left over or risky.

## Rules
- Never say it is fixed without a run that shows it.
- If the cause is in a dependency or an environment the person owns, explain it instead of patching around it.
- Ask before anything destructive.
