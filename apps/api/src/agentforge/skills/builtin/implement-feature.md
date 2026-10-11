---
name: implement-feature
description: Plan and build a new feature, endpoint, component, script or small project, then verify that it works. Use when asked to add, build, create or implement something in code.
---
# Implement a feature

## 1. Clarify only what blocks you
- Get the goal, inputs and outputs, constraints and what "done" means. If something small is missing, assume sensibly and say what you assumed.

## 2. Learn the project before writing
- Look at the layout (ls, find), the conventions (README, lint, formatter and test configuration) and a similar existing feature to mirror (grep).
- Follow its patterns, naming, error handling and dependencies. Do not add a library when the project already has one for the job.

## 3. Plan in a few lines
- Name the files you will touch, the steps, and how you will verify the result. For large work, build in vertical slices that each run.

## 4. Implement
- Use edit for existing files and write for new ones. Keep functions small, handle empty and error cases at the boundaries, validate untrusted input, and keep secrets out of code.

## 5. Verify as you go
- Run the build, type-check, linter and the relevant tests. Run the feature itself: a script, a unit test, or a request to a local server started in the background. Fix what fails.

## 6. Test it
- Add tests in the project's own framework, including at least one failure case.

## 7. Finish
- Read your own diff (`git diff agentforge-base`), remove debugging leftovers, and update docs or example configuration if the behaviour is visible to users.

## 8. Report
- Say what you built, which files changed, how you checked it (commands and results), what you assumed, and what could follow. If a repository is selected, offer to open a pull request but do not open one unasked.

## Rules
- No placeholder or TODO stubs presented as finished work.
- Never write "this should work" without a run that shows it.
