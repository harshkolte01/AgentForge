/** Plain-language labels for the tool calls an agent makes. Run internals other than these stay hidden. */
export type StepState = "running" | "done" | "failed";

/** [while running, finished, failed] */
const labels: Record<string, readonly [string, string, string]> = {
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
  write_sandbox_file: ["Editing code", "Edited code", "Couldn't edit code"],
  edit_sandbox_file: ["Editing code", "Edited code", "Couldn't edit code"],
  git_diff: ["Reviewing changes", "Reviewed changes", "Couldn't review changes"],
  run_tests: ["Running tests", "Ran tests", "Tests couldn't run"],
  run_sandbox_command: ["Running a command", "Ran a command", "Command failed"],
  get_test_results: ["Checking test results", "Checked test results", "Couldn't check test results"],
  create_task_artifact: ["Writing a document", "Wrote a document", "Couldn't write the document"],
  read_task_artifact: ["Proofreading the document", "Proofread the document", "Couldn't proofread the document"],
};

const stateIndex: Record<StepState, 0 | 1 | 2> = { running: 0, done: 1, failed: 2 };

export function labelFor(tool: string, state: StepState): string | null {
  return labels[tool]?.[stateIndex[state]] ?? null;
}

/** Tools worth counting as an action. Reading the person's own message and similar internals are not. */
export function actionCount(steps: readonly { tool: string }[]): number {
  return steps.filter((step) => step.tool in labels).length;
}
