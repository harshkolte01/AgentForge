/**
 * How each tool call is presented: the single source of truth for labels, outcome wording, counting and grouping. The
 * reducer, the history counts and the compat layer all read it, so a count and the list it counts cannot disagree.
 *
 * Wording for the three original states (running / done / failed) is unchanged from before outcomes existed. Cancelled,
 * blocked and unknown outcomes are derived from the same entry, and execution tools (tests, commands, test results)
 * derive their label from the evidence the server copied (exit code, timeout, qualification): "the tool returned"
 * never reads as "the tests passed".
 */
import type { PresentationKind, StatusIcon, Tone, ToolCallOutcome } from "@/lib/activity/types";

type ToolSpec = {
  presentation: Exclude<PresentationKind, "hidden">;
  /** While running, finished, failed: today's wording. */
  labels: readonly [running: string, done: string, failed: string];
  /** The action as an infinitive, for "Couldn't <base>: not allowed". */
  base: string;
  /** The line for `count` finished calls of this tool, when a run of them folds into one. */
  plural?: (count: number) => string;
  /** How evidence changes the label of a finished call. */
  evidence?: "tests" | "command" | "results";
};

const registry: Record<string, ToolSpec> = {
  search_conversation_history: { presentation: "status", labels: ["Searching earlier messages", "Searched earlier messages", "Couldn't search earlier messages"], base: "search earlier messages" },
  read_context_resource: { presentation: "status", labels: ["Reading earlier context", "Read earlier context", "Couldn't read earlier context"], base: "read earlier context" },
  web_search: { presentation: "status", labels: ["Searching the web", "Searched the web", "Web search failed"], base: "search the web" },
  fetch_webpage: { presentation: "status", labels: ["Reading a web page", "Read a web page", "Couldn't read a web page"], base: "read a web page" },
  browser_public_page: { presentation: "status", labels: ["Opening a page", "Opened a page", "Couldn't open a page"], base: "open a page" },
  read_research_source: { presentation: "status", labels: ["Reading a source", "Read a source", "Couldn't read a source"], base: "read a source", plural: (count) => `Read ${count} sources` },
  read_repo_file: { presentation: "status", labels: ["Reading a file", "Read a file", "Couldn't read a file"], base: "read a file" },
  search_repo: { presentation: "status", labels: ["Searching the repository", "Searched the repository", "Repository search failed"], base: "search the repository" },
  list_repo_files: { presentation: "status", labels: ["Browsing the repository", "Browsed the repository", "Couldn't browse the repository"], base: "browse the repository" },
  get_issue: { presentation: "status", labels: ["Reading an issue", "Read an issue", "Couldn't read the issue"], base: "read the issue" },
  get_pr_status: { presentation: "status", labels: ["Checking a pull request", "Checked a pull request", "Couldn't check the pull request"], base: "check the pull request" },
  read_sandbox_file: { presentation: "status", labels: ["Reading code", "Read code", "Couldn't read code"], base: "read code", plural: (count) => `Read ${count} files` },
  write_sandbox_file: { presentation: "status", labels: ["Writing code", "Wrote code", "Couldn't write code"], base: "write code" },
  edit_sandbox_file: { presentation: "status", labels: ["Editing code", "Edited code", "Couldn't edit code"], base: "edit code" },
  git_diff: { presentation: "diff", labels: ["Reviewing changes", "Reviewed changes", "Couldn't review changes"], base: "review changes" },
  run_tests: { presentation: "execution", labels: ["Running tests", "Ran tests", "Tests couldn't run"], base: "run tests", evidence: "tests" },
  run_sandbox_command: { presentation: "execution", labels: ["Running a command", "Ran a command", "Command failed"], base: "run a command", evidence: "command" },
  get_test_results: { presentation: "execution", labels: ["Checking test results", "Checked test results", "Couldn't check test results"], base: "check test results", plural: (count) => `Checked test results ${count} times`, evidence: "results" },
  // The coding tools, working in the conversation's sandbox. A command's own words and exit code say what happened.
  bash: { presentation: "execution", labels: ["Running a command", "Ran a command", "Command failed"], base: "run a command", evidence: "command" },
  read: { presentation: "status", labels: ["Reading a file", "Read a file", "Couldn't read the file"], base: "read the file", plural: (count) => `Read ${count} files` },
  write: { presentation: "status", labels: ["Writing a file", "Wrote a file", "Couldn't write the file"], base: "write the file" },
  edit: { presentation: "status", labels: ["Editing a file", "Edited a file", "Couldn't edit the file"], base: "edit the file" },
  grep: { presentation: "status", labels: ["Searching the code", "Searched the code", "Couldn't search the code"], base: "search the code" },
  find: { presentation: "status", labels: ["Finding files", "Found files", "Couldn't find files"], base: "find files" },
  ls: { presentation: "status", labels: ["Listing files", "Listed files", "Couldn't list files"], base: "list files" },
  create_pull_request: { presentation: "status", labels: ["Opening a pull request", "Opened a pull request", "Couldn't open the pull request"], base: "open the pull request" },
  create_task_artifact: { presentation: "artifact", labels: ["Preparing a download", "Created a download", "Couldn't create the download"], base: "create the download" },
  read_task_artifact: { presentation: "artifact", labels: ["Proofreading the document", "Proofread the document", "Couldn't proofread the document"], base: "proofread the document", plural: (count) => `Proofread the document ${count} times` },
};

/** Internals that never reach the timeline or its counts: reading the person's own request, and handing in the result. */
const hidden = new Set(["read_task_input", "submit_result", "unknown"]);
/** Tool names are server constants; anything else is not safe to put on screen. */
const safeName = /^[a-z][a-z0-9_-]{0,63}$/;

export type UnlistedPolicy = "generic" | "hide";

/** The tools with an entry in the registry. */
export function listedTools(): string[] {
  return Object.keys(registry);
}

/**
 * How a tool is presented. Registered-but-unlisted tools are `status` rows with a generic label (the backend emits events
 * only for registered tools, so an unknown name is a tool added after this client was built); with `unlisted: "hide"`
 * they are hidden instead, which is what the legacy `buildActivity` did.
 */
export function toolPresentation(tool: string, unlisted: UnlistedPolicy = "generic"): PresentationKind {
  if (hidden.has(tool)) return "hidden";
  const spec = registry[tool];
  if (spec) return spec.presentation;
  return unlisted === "generic" && safeName.test(tool) ? "status" : "hidden";
}

/** Whether a call of this tool is an action: it appears in the timeline and in every count. */
export function isActionTool(tool: string, unlisted: UnlistedPolicy = "generic"): boolean {
  return toolPresentation(tool, unlisted) !== "hidden";
}

/** "read_research_source" -> "read research source". */
export function humanizeTool(tool: string): string {
  return tool.replaceAll(/[_-]+/g, " ").trim().toLowerCase();
}

/**
 * The plugin a tool belongs to, as a name to show: plugin tools are named `<plugin>__<tool>` (the part before is the plugin's
 * short name, chosen when it was connected). Null for any other tool.
 */
export function pluginName(tool: string): string | null {
  const index = tool.indexOf("__");
  if (index < 1 || index + 2 >= tool.length) return null;
  const name = tool.slice(0, index).replaceAll("-", " ");
  return name.charAt(0).toUpperCase() + name.slice(1);
}

/** The generic spec for a tool the registry does not list: "Used <tool name>", or "Used <plugin>" for a plugin's tool. */
function genericSpec(tool: string): ToolSpec {
  const plugin = pluginName(tool);
  if (plugin) return { presentation: "status", base: `use ${plugin}`, labels: [`Using ${plugin}`, `Used ${plugin}`, `Couldn't use ${plugin}`] };
  const name = humanizeTool(tool);
  return {
    presentation: "status", base: `use ${name}`,
    labels: [`Using ${name}`, `Used ${name}`, `Couldn't use ${name}`],
  };
}

const lowerFirst = (text: string) => text.charAt(0).toLowerCase() + text.slice(1);

export type CallFacts = {
  tool: string;
  outcome: ToolCallOutcome;
  /** The detail from the start event, as sent (a command id such as `node_build` tells builds from other commands). */
  rawDetail?: string | null;
  /** Evidence from the finish event; only execution tools carry it. */
  exitCode?: number | null;
  timedOut?: boolean | null;
  qualified?: boolean | null;
};

export type CallPresentation = { label: string; detail?: string; tone: Tone; icon: StatusIcon };

type Verdict = "passed" | "failed" | "timed_out" | "unqualified";

/** What the execution evidence says, or null when the finish event carried none. A timeout beats an exit code, a failing exit beats qualification. */
function verdictOf(facts: CallFacts): Verdict | null {
  if (facts.timedOut === true) return "timed_out";
  if (typeof facts.exitCode === "number" && facts.exitCode !== 0) return "failed";
  if (facts.qualified === false) return "unqualified";
  if (facts.exitCode === 0) return "passed";
  return null;
}

const isBuild = (commandId: string | null | undefined) => !!commandId && /(^|[_-])build($|[_-])/.test(commandId);

const evidenceLabels: Record<Verdict, { tests: string; build: string; command: string | null; results: string }> = {
  passed: { tests: "Tests passed", build: "Build passed", command: null, results: "Checked test results: passed" },
  failed: { tests: "Tests failed", build: "Build failed", command: "Command failed", results: "Checked test results: failed" },
  timed_out: { tests: "Tests timed out", build: "Build timed out", command: "Command timed out", results: "Checked test results: timed out" },
  unqualified: { tests: "Ran tests (result not qualified)", build: "Ran build (result not qualified)", command: "Ran a command (result not qualified)", results: "Checked test results: not qualified" },
};

const verdictTone: Record<Verdict, Tone> = { passed: "success", failed: "danger", timed_out: "danger", unqualified: "warning" };
const verdictIcon: Record<Verdict, StatusIcon> = { passed: "success", failed: "failed", timed_out: "failed", unqualified: "warning" };

/** How a call that has no evidence looks, by outcome. */
const outcomeStyle: Record<ToolCallOutcome, { tone: Tone; icon: StatusIcon }> = {
  running: { tone: "neutral", icon: "running" },
  completed: { tone: "success", icon: "success" },
  failed: { tone: "danger", icon: "failed" },
  // Refused by policy, not broken: a caution, not an error.
  blocked: { tone: "warning", icon: "blocked" },
  cancelled: { tone: "neutral", icon: "stopped" },
  unknown: { tone: "warning", icon: "unknown" },
};

/** The text under a label: a search query in quotes, anything else as sent. */
export function formatDetail(tool: string, detail: string | null | undefined): string | undefined {
  if (!detail) return undefined;
  return tool === "web_search" ? `“${detail}”` : detail;
}

/**
 * The label, detail, tone and icon of one call in its current outcome. Null for hidden tools. The label for a finished
 * execution tool comes from its evidence; every other label is wording only.
 */
export function describeCall(facts: CallFacts, unlisted: UnlistedPolicy = "generic"): CallPresentation | null {
  if (toolPresentation(facts.tool, unlisted) === "hidden") return null;
  const spec = registry[facts.tool] ?? genericSpec(facts.tool);
  const [running, done, failed] = spec.labels;
  const detail = formatDetail(facts.tool, facts.rawDetail);
  const style = outcomeStyle[facts.outcome];
  let label: string;
  switch (facts.outcome) {
    case "running": label = running; break;
    case "failed": label = failed; break;
    case "cancelled": label = `Stopped ${lowerFirst(running)}`; break;
    case "blocked": label = `Couldn't ${spec.base}: not allowed`; break;
    case "unknown": label = `${running} may not have finished`; break;
    case "completed": {
      const verdict = spec.evidence ? verdictOf(facts) : null;
      if (spec.evidence && verdict) {
        const kind = spec.evidence === "command" ? (isBuild(facts.rawDetail) ? "build" : "command") : spec.evidence;
        const text = evidenceLabels[verdict][kind];
        return { label: text ?? done, detail, tone: verdictTone[verdict], icon: verdictIcon[verdict] };
      }
      label = done;
    }
  }
  return { label, detail, tone: style.tone, icon: style.icon };
}

/** Whether finished calls of a tool can fold into one counted line. */
export function isGroupable(tool: string): boolean {
  return !!registry[tool]?.plural;
}

/** The line for `count` finished calls of `tool`, or null when that tool's calls do not fold. */
export function groupLabel(tool: string, count: number): string | null {
  return registry[tool]?.plural?.(count) ?? null;
}

/**
 * Whether a call may fold into a group. Calls with their own detail say something the count would hide, and a call whose
 * evidence is bad (failing tests) must stay visible on its own.
 */
export function canFold(call: { tool: string; outcome: ToolCallOutcome; rawDetail: string | null; tone: Tone }): boolean {
  return call.outcome === "completed" && !call.rawDetail && isGroupable(call.tool) && (call.tone === "success" || call.tone === "neutral");
}

/**
 * The heading for an agent that starts a new phase of the run, or null when it only does the main work. The main work needs
 * no heading: its notes and steps are the story, and a label that stays "running" for the whole stage reads as stuck.
 */
export function phaseLabel(type: string): string | null {
  const [namespace, ...rest] = type.split("/");
  const name = rest[rest.length - 1] ?? type;
  if (namespace === "project") return `Running “${name.replaceAll("-", " ")}”`;
  switch (name) {
    case "verifier": return "Checking the result";
    case "code-reviewer": return "Reviewing the change";
    case "code-verifier": return "Verifying the change";
    default: return null;
  }
}

/** Whether a call's details can be requested: an execution, diff or artifact call that has a persisted id and has settled. */
export function isDetailable(presentation: PresentationKind, toolCallId: string | null, outcome: ToolCallOutcome): boolean {
  return (presentation === "execution" || presentation === "diff" || presentation === "artifact") && !!toolCallId && outcome !== "running";
}

const outcomes = new Set<string>(["completed", "failed", "blocked", "cancelled", "unknown"]);

/**
 * The outcome a `tool.finished` payload reports. Older events carry only `status`: ok means completed, anything else failed
 * (they cannot say cancelled, blocked or unknown, and nothing is invented).
 */
export function outcomeOf(payload: Record<string, unknown>): Exclude<ToolCallOutcome, "running"> {
  if (typeof payload.outcome === "string" && outcomes.has(payload.outcome)) return payload.outcome as Exclude<ToolCallOutcome, "running">;
  return payload.status === "ok" ? "completed" : "failed";
}
