import assert from "node:assert/strict";
import { test } from "node:test";
import { copyName, formatSkillMarkdown, nameProblem, parseSkillMarkdown, usable, withSkill } from "../lib/skills-api.ts";
import { createSkillsStore } from "../lib/skills-store.ts";
import { describeCall } from "../lib/activity/presentation.ts";
import { settingsSection, settingsSections } from "../features/settings/settings-sections.ts";

const skill = (name, extra = {}) => ({ key: `custom:${name}`, source: "custom", name, description: `Does ${name} when asked.`, enabled: true, updated_at: null, ...extra });
const builtin = (name, extra = {}) => skill(name, { key: `builtin:${name}`, source: "builtin", ...extra });
const overview = (custom = [], built = [], extra = {}) => ({ enabled: true, max_custom: 50, builtin: built, custom, ...extra });
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

test("a skill name is short, lowercase and hyphenated, and the reason is given when it is not", () => {
  for (const name of ["abc", "code-review", "a1-b2", "x".repeat(40), " padded-name "]) assert.equal(nameProblem(name), null, name);
  for (const name of ["ab", "x".repeat(41), "Code-Review", "1abc", "-abc", "abc-", "a--b", "a b", "a_b", "/review", ""]) assert.match(nameProblem(name) ?? "", /3-40 characters/, name);
});

test("a SKILL.md is read into its name, description and instructions", () => {
  const parsed = parseSkillMarkdown("---\nname: my-skill\ndescription: \"Does a thing when asked.\"\nversion: 2\n---\n# Title\n\nSteps here.\n");
  assert.deepEqual(parsed, { name: "my-skill", description: "Does a thing when asked.", instructions: "# Title\n\nSteps here." });
  assert.deepEqual(parseSkillMarkdown("﻿---\r\nname: a-skill\r\ndescription: 'x'\r\n---\r\nBody\r\n"), { name: "a-skill", description: "x", instructions: "Body" });
});

test("a file without front matter is all instructions, and a header that never closes is too", () => {
  assert.deepEqual(parseSkillMarkdown("# Just notes\n\nDo this.\n"), { instructions: "# Just notes\n\nDo this." });
  assert.deepEqual(parseSkillMarkdown("---\nname: x\nno end"), { instructions: "---\nname: x\nno end" });
});

test("dashes inside the instructions stay in them", () => {
  const parsed = parseSkillMarkdown("---\nname: a-skill\ndescription: d\n---\nFirst\n\n---\n\nSecond");
  assert.equal(parsed.instructions, "First\n\n---\n\nSecond");
});

test("exporting a skill and importing it again gives the same skill", () => {
  const fields = { name: "my-skill", description: "Does a thing\nwhen asked.", instructions: "# Title\n\nStep one.\n\n---\n\nStep two." };
  const text = formatSkillMarkdown(fields);
  assert.equal(text, "---\nname: my-skill\ndescription: Does a thing when asked.\n---\n# Title\n\nStep one.\n\n---\n\nStep two.\n");
  assert.deepEqual(parseSkillMarkdown(text), { ...fields, description: "Does a thing when asked." });
});

test("choosing a skill puts /name at the front of the message and never stacks two", () => {
  assert.equal(withSkill("", "code-review"), "/code-review ");
  assert.equal(withSkill("look at my diff", "code-review"), "/code-review look at my diff");
  assert.equal(withSkill("/deep-research what is new", "code-review"), "/code-review what is new");
  assert.equal(withSkill("/deep", "code-review"), "/code-review ");
  assert.equal(withSkill("see /etc/hosts", "code-review"), "/code-review see /etc/hosts");
  assert.equal(withSkill("x".repeat(9000), "code-review").length, 8000);
});

test("a copy of a skill gets a name that is neither the original nor too long", () => {
  assert.equal(copyName("code-review"), "code-review-custom");
  const long = copyName("x".repeat(40));
  assert.ok(long.length <= 40 && long.endsWith("-custom"));
  assert.equal(nameProblem(long), null);
  assert.equal(nameProblem(copyName("a".repeat(32) + "-bc")), null);
});

test("the composer offers switched-on skills, the person's own first, and nothing when skills are off", () => {
  const all = overview([skill("mine"), skill("hidden", { enabled: false })], [builtin("deep-research"), builtin("code-review", { enabled: false })]);
  assert.deepEqual(usable(all).map((item) => item.name), ["mine", "deep-research"]);
  assert.deepEqual(usable(overview([skill("mine")], [], { enabled: false })), []);
  assert.deepEqual(usable(null), []);
});

test("the store reads once, applies a changed skill without its instructions, and removes a deleted one", async () => {
  let reads = 0;
  const store = createSkillsStore({ fetch: async () => { reads += 1; return overview([skill("mine")], [builtin("code-review")]); }, describe: String });
  store.load(); store.load();
  await tick();
  assert.equal(reads, 1);
  store.load();
  assert.equal(reads, 1);
  store.apply(builtin("code-review", { enabled: false, instructions: "SECRET BODY" }));
  assert.equal(store.getSnapshot().overview.builtin[0].enabled, false);
  assert.equal("instructions" in store.getSnapshot().overview.builtin[0], false, "a list never carries instructions");
  store.apply(skill("second"));
  store.apply(skill("mine", { description: "Changed description for it." }));
  assert.deepEqual(store.getSnapshot().overview.custom.map((item) => [item.name, item.description]), [["mine", "Changed description for it."], ["second", "Does second when asked."]]);
  store.remove("custom:mine");
  assert.deepEqual(store.getSnapshot().overview.custom.map((item) => item.name), ["second"]);
  store.reset();
  assert.equal(store.getSnapshot().status, "idle");
});

test("a read overtaken by a change does not bring the old list back, and a failed read can be tried again", async () => {
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  let calls = 0;
  const store = createSkillsStore({
    fetch: async () => { calls += 1; if (calls === 1) return overview([skill("mine")]); if (calls === 2) { await gate; return overview([skill("mine")]); } throw new Error("down"); },
    describe: (failure) => `Could not read: ${failure.message}`,
  });
  store.load();
  await tick();
  store.reload();
  store.apply(skill("added"));
  release();
  await tick();
  assert.deepEqual(store.getSnapshot().overview.custom.map((item) => item.name), ["mine", "added"]);
  store.reload();
  await tick();
  assert.equal(store.getSnapshot().status, "error");
  assert.equal(store.getSnapshot().error, "Could not read: down");
  assert.ok(store.getSnapshot().overview, "what was known stays");
});

test("loading a skill reads as a step of its own in the timeline", () => {
  assert.equal(describeCall({ tool: "use_skill", outcome: "running", rawDetail: "code-review" }).label, "Loading a skill");
  assert.equal(describeCall({ tool: "use_skill", outcome: "running", rawDetail: "code-review" }).detail, "code-review");
  assert.equal(describeCall({ tool: "use_skill", outcome: "completed" }).label, "Loaded a skill");
  assert.equal(describeCall({ tool: "use_skill", outcome: "failed" }).label, "Couldn't load the skill");
});

test("Skills lives on the Plugins page, not in Settings", () => {
  assert.ok(!settingsSections.some((item) => item.id === "skills"));
  assert.equal(settingsSection("skills"), "account");
});
