---
name: explore-codebase
description: Understand an unfamiliar repository or code area and explain how it works: structure, entry points, data flow and conventions. Use when asked to explain, map, onboard onto, or find where something is implemented in a codebase.
---
# Explore a codebase

## 1. Start broad and cheap
- With a selected repository, list_repo_files with max_depth 2 gives the layout. Then read the README and docs, the manifest (package.json, pyproject.toml, go.mod, Cargo.toml and similar) and the CI configuration.
- With the workspace, use ls, find and grep for the same purpose.

## 2. Build a picture
- Identify the languages and frameworks, how to build, run and test it, the top-level modules and what each is responsible for, the entry points (main, servers, CLIs, routes, workers), configuration and environment variables, data stores and outside services.

## 3. Trace the actual question
- For "where is X done" or "how does X work": search names, strings, routes and configuration keys (search_repo or grep). Open the definition, then its callers. Follow one concrete path from start to end, for example request, handler, logic, storage, response.
- Use `git log` and blame when the history explains why something is the way it is.

## 4. Read, do not guess
- Back every claim with a path and line number (path:line). When you are inferring, say so.

## 5. Explain
- Give a three-to-five sentence overview, then a short map of the key directories and files, then the flow that was asked about step by step.
- Add the conventions and gotchas that matter (naming, error handling, tests, generated code) and where to start for a change.
- Stay within the scope of the question and offer deeper dives.

## Rules
- Do not change files while exploring. If you notice bugs or smells, list them briefly at the end as observations.
