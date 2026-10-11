import assert from "node:assert/strict";
import { test } from "node:test";
import {
  canFold, describeCall, groupLabel, humanizeTool, isActionTool, isDetailable, listedTools, outcomeOf, phaseLabel, toolPresentation,
} from "../lib/activity/presentation.ts";
import { actionCount, labelFor, pluralLabelFor } from "../lib/tool-steps.ts";

// The wording of the original three states, exactly as it shipped before outcomes existed: [running, done, failed].
// The two conversation-memory tools were added to the registry afterwards (they were previously unlisted and hidden by the old labels).
const original = {
  search_conversation_history: ["Searching earlier messages", "Searched earlier messages", "Couldn't search earlier messages"],
  read_context_resource: ["Reading earlier context", "Read earlier context", "Couldn't read earlier context"],
  web_search: ["Searching the web", "Searched the web", "Web search failed"],
  fetch_webpage: ["Reading a web page", "Read a web page", "Couldn't read a web page"],
  browser_public_page: ["Opening a page", "Opened a page", "Couldn't open a page"],
  read_research_source: ["Reading a source", "Read a source", "Couldn't read a source"],
  read_repo_file: ["Reading a file", "Read a file", "Couldn't read a file"],
  search_repo: ["Searching the repository", "Searched the repository", "Repository search failed"],
  list_repo_files: ["Browsing the repository", "Browsed the repository", "Couldn't browse the repository"],
  get_issue: ["Reading an issue", "Read an issue", "Couldn't read the issue"],
  get_pr_status: ["Checking a pull request", "Checked a pull request", "Couldn't check the pull request"],
  read_sandbox_file: ["Reading code", "Read code", "Couldn't read code"],
  write_sandbox_file: ["Writing code", "Wrote code", "Couldn't write code"],
  edit_sandbox_file: ["Editing code", "Edited code", "Couldn't edit code"],
  git_diff: ["Reviewing changes", "Reviewed changes", "Couldn't review changes"],
  run_tests: ["Running tests", "Ran tests", "Tests couldn't run"],
  run_sandbox_command: ["Running a command", "Ran a command", "Command failed"],
  get_test_results: ["Checking test results", "Checked test results", "Couldn't check test results"],
  bash: ["Running a command", "Ran a command", "Command failed"],
  read: ["Reading a file", "Read a file", "Couldn't read the file"],
  write: ["Writing a file", "Wrote a file", "Couldn't write the file"],
  edit: ["Editing a file", "Edited a file", "Couldn't edit the file"],
  grep: ["Searching the code", "Searched the code", "Couldn't search the code"],
  find: ["Finding files", "Found files", "Couldn't find files"],
  ls: ["Listing files", "Listed files", "Couldn't list files"],
  create_pull_request: ["Opening a pull request", "Opened a pull request", "Couldn't open the pull request"],
  create_task_artifact: ["Preparing a download", "Created a download", "Couldn't create the download"],
  read_task_artifact: ["Proofreading the document", "Proofread the document", "Couldn't proofread the document"],
  // Added with skills: the agent loads a skill's instructions before following them.
  use_skill: ["Loading a skill", "Loaded a skill", "Couldn't load the skill"],
};
const label = (facts) => describeCall(facts)?.label;

test("every registered tool keeps today's wording for running, finished and failed", () => {
  assert.deepEqual(listedTools().sort(), Object.keys(original).sort());
  for (const [tool, [running, done, failed]] of Object.entries(original)) {
    assert.equal(label({ tool, outcome: "running" }), running, `${tool} running`);
    assert.equal(label({ tool, outcome: "completed" }), done, `${tool} completed`);
    assert.equal(label({ tool, outcome: "failed" }), failed, `${tool} failed`);
    assert.equal(labelFor(tool, "running"), running);
    assert.equal(labelFor(tool, "done"), done);
    assert.equal(labelFor(tool, "failed"), failed);
  }
});

test("every registered tool has cancelled, blocked and unknown wording that names the action", () => {
  const rows = [];
  for (const tool of listedTools()) {
    const [running] = original[tool];
    const stopped = label({ tool, outcome: "cancelled" });
    const blocked = label({ tool, outcome: "blocked" });
    const unknown = label({ tool, outcome: "unknown" });
    assert.equal(stopped, `Stopped ${running[0].toLowerCase()}${running.slice(1)}`, tool);
    assert.match(blocked, /^Couldn't .+: not allowed$/, tool);
    assert.equal(unknown, `${running} may not have finished`, tool);
    rows.push([tool, stopped, blocked, unknown]);
  }
  // A few spot checks of the finished sentences.
  const byTool = Object.fromEntries(rows.map(([tool, ...rest]) => [tool, rest]));
  assert.deepEqual(byTool.web_search, ["Stopped searching the web", "Couldn't search the web: not allowed", "Searching the web may not have finished"]);
  assert.deepEqual(byTool.run_tests, ["Stopped running tests", "Couldn't run tests: not allowed", "Running tests may not have finished"]);
});

test("outcomes carry a tone and an icon, so failure never relies on colour alone", () => {
  const look = (outcome) => { const shown = describeCall({ tool: "web_search", outcome }); return [shown.tone, shown.icon]; };
  assert.deepEqual(look("running"), ["neutral", "running"]);
  assert.deepEqual(look("completed"), ["success", "success"]);
  assert.deepEqual(look("failed"), ["danger", "failed"]);
  assert.deepEqual(look("blocked"), ["warning", "blocked"]);
  assert.deepEqual(look("cancelled"), ["neutral", "stopped"]);
  assert.deepEqual(look("unknown"), ["warning", "unknown"]);
});

test("a search query is quoted; other details are shown as sent", () => {
  assert.equal(describeCall({ tool: "web_search", outcome: "completed", rawDetail: "agent harness" }).detail, "“agent harness”");
  assert.equal(describeCall({ tool: "read_repo_file", outcome: "completed", rawDetail: "src/a.py" }).detail, "src/a.py");
  assert.equal(describeCall({ tool: "git_diff", outcome: "completed" }).detail, undefined);
});

test("tests: the label comes from the evidence, never from the tool having returned", () => {
  const tests = (extra) => describeCall({ tool: "run_tests", outcome: "completed", rawDetail: "python_pytest", ...extra });
  assert.deepEqual([tests({ exitCode: 0, qualified: true }).label, tests({ exitCode: 0, qualified: true }).tone], ["Tests passed", "success"]);
  assert.equal(tests({ exitCode: 0 }).label, "Tests passed"); // qualified unknown is not unqualified
  assert.deepEqual([tests({ exitCode: 1, qualified: true }).label, tests({ exitCode: 1, qualified: true }).tone, tests({ exitCode: 1 }).icon], ["Tests failed", "danger", "failed"]);
  assert.deepEqual([tests({ timedOut: true }).label, tests({ timedOut: true }).tone], ["Tests timed out", "danger"]);
  assert.equal(tests({ timedOut: true, exitCode: 0 }).label, "Tests timed out");
  assert.deepEqual([tests({ exitCode: 0, qualified: false }).label, tests({ exitCode: 0, qualified: false }).tone, tests({ exitCode: 0, qualified: false }).icon], ["Ran tests (result not qualified)", "warning", "warning"]);
  assert.equal(tests({ exitCode: 2, qualified: false }).label, "Tests failed"); // failing beats qualification
  assert.equal(tests({}).label, "Ran tests"); // no evidence (older events): the neutral wording, never "passed"
  assert.equal(tests({}).tone, "success");
  // The tool failing to run at all is a different thing from failing tests.
  assert.equal(describeCall({ tool: "run_tests", outcome: "failed" }).label, "Tests couldn't run");
});

test("builds are told apart by the command id and read the same way", () => {
  const build = (extra) => describeCall({ tool: "run_sandbox_command", outcome: "completed", rawDetail: "node_build", ...extra });
  assert.equal(build({ exitCode: 0, qualified: true }).label, "Build passed");
  assert.equal(build({ exitCode: 1 }).label, "Build failed");
  assert.equal(build({ timedOut: true }).label, "Build timed out");
  assert.equal(build({ exitCode: 0, qualified: false }).label, "Ran build (result not qualified)");
  assert.equal(build({}).label, "Ran a command");
  const other = (extra) => describeCall({ tool: "run_sandbox_command", outcome: "completed", rawDetail: "lint", ...extra });
  assert.equal(other({ exitCode: 0 }).label, "Ran a command");
  assert.equal(other({ exitCode: 3 }).label, "Command failed");
  assert.equal(other({ timedOut: true }).label, "Command timed out");
  assert.equal(other({ exitCode: 0, qualified: false }).label, "Ran a command (result not qualified)");
});

test("checking stored test results reads the stored evidence", () => {
  const results = (extra) => describeCall({ tool: "get_test_results", outcome: "completed", ...extra }).label;
  assert.equal(results({ exitCode: 0, qualified: true }), "Checked test results: passed");
  assert.equal(results({ exitCode: 1 }), "Checked test results: failed");
  assert.equal(results({ timedOut: true }), "Checked test results: timed out");
  assert.equal(results({ exitCode: 0, qualified: false }), "Checked test results: not qualified");
  assert.equal(results({}), "Checked test results");
});

test("evidence only matters for execution tools and only once the call completed", () => {
  assert.equal(describeCall({ tool: "web_search", outcome: "completed", exitCode: 1 }).label, "Searched the web");
  assert.equal(describeCall({ tool: "run_tests", outcome: "blocked", exitCode: 0 }).label, "Couldn't run tests: not allowed");
  assert.equal(describeCall({ tool: "run_tests", outcome: "unknown", exitCode: 0 }).label, "Running tests may not have finished");
});

test("presentation kinds follow the plan's table", () => {
  const kinds = Object.fromEntries(listedTools().map((tool) => [tool, toolPresentation(tool)]));
  assert.deepEqual(kinds, {
    search_conversation_history: "status", read_context_resource: "status",
    web_search: "status", fetch_webpage: "status", browser_public_page: "status", read_research_source: "status", read_repo_file: "status",
    search_repo: "status", list_repo_files: "status", get_issue: "status", get_pr_status: "status", read_sandbox_file: "status",
    write_sandbox_file: "status", edit_sandbox_file: "status", git_diff: "diff", run_tests: "execution", run_sandbox_command: "execution",
    get_test_results: "execution", bash: "execution", read: "status", write: "status", edit: "status", grep: "status", find: "status",
    ls: "status", create_pull_request: "status", create_task_artifact: "artifact", read_task_artifact: "artifact", use_skill: "status",
  });
  for (const tool of ["read_task_input", "submit_result", "unknown", "Weird Name", "", "../x"]) assert.equal(toolPresentation(tool), "hidden", tool);
});

test("a registered tool without an entry gets a safe generic label and counts; hidden tools never count", () => {
  assert.equal(toolPresentation("fancy_new_tool"), "status");
  assert.deepEqual(
    ["running", "completed", "failed", "cancelled", "blocked", "unknown"].map((outcome) => label({ tool: "fancy_new_tool", outcome })),
    ["Using fancy new tool", "Used fancy new tool", "Couldn't use fancy new tool", "Stopped using fancy new tool", "Couldn't use fancy new tool: not allowed", "Using fancy new tool may not have finished"],
  );
  assert.equal(humanizeTool("fancy_new-tool"), "fancy new tool");
  assert.equal(isActionTool("fancy_new_tool"), true);
  assert.equal(isActionTool("fancy_new_tool", "hide"), false);
  assert.equal(describeCall({ tool: "read_task_input", outcome: "completed" }), null);
  assert.equal(isActionTool("read_task_input"), false);
  assert.equal(isActionTool("web_search"), true);
  // The compat counter follows the same rules as the reducer.
  assert.equal(actionCount([{ tool: "web_search" }, { tool: "read_task_input" }, { tool: "fancy_new_tool" }, { tool: "unknown" }, { tool: "run_tests" }]), 3);
  assert.equal(labelFor("fancy_new_tool", "done"), null); // the legacy helper never knew unlisted tools
});

test("only some finished calls fold into a counted line", () => {
  assert.equal(groupLabel("read_research_source", 4), "Read 4 sources");
  assert.equal(groupLabel("read_sandbox_file", 3), "Read 3 files");
  assert.equal(groupLabel("read_task_artifact", 2), "Proofread the document 2 times");
  assert.equal(groupLabel("get_test_results", 5), "Checked test results 5 times");
  assert.equal(groupLabel("web_search", 2), null);
  assert.equal(pluralLabelFor("read_research_source", 2), "Read 2 sources");
  const call = (extra) => ({ tool: "read_research_source", outcome: "completed", rawDetail: null, tone: "success", ...extra });
  assert.equal(canFold(call({})), true);
  assert.equal(canFold(call({ rawDetail: "src/a.py" })), false); // has its own detail
  assert.equal(canFold(call({ outcome: "failed", tone: "danger" })), false);
  assert.equal(canFold(call({ tool: "get_test_results", tone: "danger" })), false); // a bad result stays visible on its own line
  assert.equal(canFold(call({ tool: "web_search" })), false);
});

test("details can be requested for settled execution, diff and artifact calls that have a persisted id", () => {
  assert.equal(isDetailable("execution", "id", "completed"), true);
  assert.equal(isDetailable("execution", "id", "unknown"), true);
  assert.equal(isDetailable("diff", "id", "failed"), true);
  assert.equal(isDetailable("artifact", "id", "completed"), true);
  assert.equal(isDetailable("execution", "id", "running"), false);
  assert.equal(isDetailable("execution", null, "completed"), false);
  assert.equal(isDetailable("status", "id", "completed"), false);
});

test("a finish event reports its outcome; older events only their status", () => {
  assert.equal(outcomeOf({ outcome: "blocked", status: "error" }), "blocked");
  assert.equal(outcomeOf({ outcome: "cancelled" }), "cancelled");
  assert.equal(outcomeOf({ outcome: "unknown" }), "unknown");
  assert.equal(outcomeOf({ status: "ok" }), "completed");
  assert.equal(outcomeOf({ status: "error" }), "failed");
  assert.equal(outcomeOf({ outcome: "running", status: "ok" }), "completed"); // "running" is never an outcome
  assert.equal(outcomeOf({ outcome: "bogus" }), "failed");
  assert.equal(outcomeOf({}), "failed");
});

test("phase headings", () => {
  assert.equal(phaseLabel("builtin/researcher"), null);
  assert.equal(phaseLabel("builtin/verifier"), "Checking the result");
  assert.equal(phaseLabel("builtin/code-reviewer"), "Reviewing the change");
  assert.equal(phaseLabel("builtin/code-verifier"), "Verifying the change");
  assert.equal(phaseLabel("project/data-cleaner"), "Running “data cleaner”");
});
