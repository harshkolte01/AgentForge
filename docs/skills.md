# Skills

A skill is a written playbook for a kind of work: a name, a one-paragraph description, and instructions. The agent does not carry
every playbook in its prompt. It is told each skill's name and description, and loads the instructions only when a request fits.
Some skills ship with the app; people also write their own.

## Using them

* **The Skills tab of the Plugins page** (sidebar → Plugins → Skills; also Settings → Skills) lists the built-in skills and the
  person's own as cards. Each has a switch. The person's own skills can be created, edited, imported from a `SKILL.md`, exported,
  and deleted. A built-in skill cannot be edited, but **Copy to my skills** makes an editable copy under a new name.
* **Typing `/name` at the start of a message** asks for that skill. Otherwise the agent decides: when a request clearly fits a skill's
  description it loads the skill first (the timeline shows "Loaded a skill").

## Built-in skills

| Name | For |
| --- | --- |
| `deep-research` | Sourced, cross-checked web research |
| `code-review` | Reviewing a diff, branch or area of code; prioritized findings |
| `debug-and-fix` | Reproducing, diagnosing and fixing a bug or failing test |
| `implement-feature` | Planning, building and verifying a feature |
| `write-tests` | Adding unit, integration or regression tests |
| `explore-codebase` | Explaining how an unfamiliar repository works |
| `pull-request` | Verifying work and opening a draft pull request |
| `write-report` | Documents and reports saved as downloadable files |
| `analyze-data` | Analysing CSV/JSON data with code in the workspace |
| `write-skill` | Helping a person write a new skill |

They are packaged files, `apps/api/src/agentforge/skills/builtin/<name>.md`, in the same `SKILL.md` format a person can import.
Adding or changing one is a code change: edit the file, and for a new one add its name to `ORDER` in `skills/builtin.py`.
`tests/test_skills.py` checks that every built-in is valid, fits one tool result, and names only tools that exist.

## The format

```
---
name: code-review
description: Review code changes and report prioritized findings. Use when asked to review a diff or pull request.
---
# Code review
...instructions in Markdown...
```

| Field | Rule |
| --- | --- |
| `name` | 3-40 characters: lowercase letters, digits and single hyphens, starting with a letter. Unique per person; a built-in's name is reserved. |
| `description` | 10-300 characters, one line. Say what the skill does **and when to use it**: it is all the agent sees before loading. |
| instructions | 20-10,000 characters, and at most 13,000 bytes once escaped as JSON, so they always fit in one tool result. |

A person may have up to `SKILL_MAX_PER_USER` (50) skills.

## How it works

| Piece | Where |
| --- | --- |
| Format, limits, `SKILL.md` parse and write | `skills/format.py` |
| Built-in set | `skills/builtin.py`, `skills/builtin/*.md` |
| Create, edit, delete, switch on/off; what is offered | `skills/service.py`, `api/routes_skills.py` |
| The `use_skill` tool and the list in the prompt | `skills/runtime.py`, `subagents/prompt_builder.py` (`SKILL_RULES`) |
| Storage | `user_skills`, `skill_settings` (migration `0016_skills`) |

At the start of each conversation turn the worker reads the skills the person has switched on and, if there are any, registers
`use_skill` and adds a "Skills" section to the system prompt (name and description of each, at most 60, plus the rules for using
them). `use_skill` is offered to every conversation run beside the tools its agent definition lists, so agent definitions did not
change. When the agent calls it, the instructions are read from the person's current settings, so a skill edited or switched off
mid-run is seen as it is now. Skills add to every request, which the composer's context meter counts.

`GET/POST /api/v1/skills`, `GET/PATCH/DELETE /api/v1/skills/{key}` with `key` = `builtin:<name>` or `custom:<id>`.

## Trust

* A skill is guidance, not authority. The prompt tells the agent that a skill never overrides the platform rules, grants
  permissions, or adds tools, and the tool result repeats it. A skill cannot make the agent skip an approval: approvals are enforced
  by the tool gateway, not by instructions.
* A person's skills are read and written only through their own account; another person's skills are never listed or loadable.
* Skill text comes only from the app's own files or from the person who owns it. Nothing a web page, file or plugin returns can
  become a skill.
* The agent cannot create or change skills. That is deliberate: a persistent instruction that a model can write is a persistent
  prompt injection.

## Operating it

`SKILLS_ENABLED` and `SKILL_MAX_PER_USER` in the [configuration reference](configuration.md#plugins-and-skills). Run `alembic upgrade head` before starting the new
API and worker. `examples/skills_smoke.py` is the disposable end-to-end check and runs with the other release checks.
