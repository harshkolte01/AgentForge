---
name: write-skill
description: Help the person write a new skill for this app: a reusable playbook the agent follows. Use when they ask to create, draft, improve or review a skill or a SKILL.md.
---
# Write a skill

Skills are added by the person in Settings → Skills. You cannot create or save one yourself: you write the text, and they add it.

## 1. Understand the job
- Find out what job the skill does, when it should apply, what a good result looks like, and any rules it must follow. Ask in one short message, and only if that is not already clear.

## 2. Produce a SKILL.md
Give it in a single fenced block, in this form:

```
---
name: short-lowercase-name
description: One or two sentences: what it does and when to use it.
---
# Title

Steps, rules and the shape of the result.
```

## 3. Rules for a good skill
- **name**: 3-40 characters, lowercase letters, digits and single hyphens, starting with a letter, verb first (review-contract). It must not match a built-in skill.
- **description**: 10-300 characters. Say what the skill does and when to use it, with the phrases the person would really use. It is the only thing the agent sees before it decides to load the skill, so make it specific.
- **instructions**: at most 10,000 characters. Write imperative, numbered steps. Be concrete about what to look at, what to produce and how to format it. Say what to do when information is missing. Include checks and a clear definition of done.
- Name a tool only if you are sure it exists (web_search, fetch_webpage, bash, read, write, edit, grep, create_task_artifact, create_pull_request).
- One skill does one job. Split big ones. Do not repeat what the agent already does well without a skill.
- Never include secrets or personal data. Do not tell the agent to skip approvals or break platform rules: that is ignored.

## 4. After the block
- In two or three lines say how to add it (Settings → Skills → New skill, or Import) and how to try it (type /skill-name before a message).

## Reviewing an existing skill
- Check it against each rule above and return a corrected version in the same format, with a short list of what you changed.
