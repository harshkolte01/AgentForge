// Event-stream fixtures shared by the activity tests. Sequences are dense, like the real log: bookkeeping events that
// the timeline ignores (task.created, model.started, ...) are included so contiguity maths is realistic.
//
// `style: "legacy"` is the wire format before tool_call_id/outcome existed (call_id only, status ok|error);
// `style: "modern"` adds tool_call_id, agent_run_id and an explicit outcome.

/** A small event-log builder: every method appends one event with the next sequence number. */
export function builder({ taskId = "task-1", style = "legacy", start = 0 } = {}) {
  let sequence = start;
  // Tool-call ids derive from the starting sequence, so a builder continuing a log (start > 0) never repeats an id.
  let callCounter = start;
  const events = [];
  const add = (event_type, payload = {}) => {
    const event = { task_id: taskId, sequence: ++sequence, event_type, payload, created_at: "2026-01-01T00:00:00Z" };
    events.push(event);
    return event;
  };
  const modern = style === "modern";
  const api = {
    events,
    get last() { return sequence; },
    add,
    /** Starts a tool call; returns a handle for finish(). */
    start(tool, detail, { callId, toolCallId } = {}) {
      const n = ++callCounter;
      const handle = { tool, call_id: callId ?? `run:tool:${n}`, tool_call_id: toolCallId ?? `00000000-0000-4000-8000-${String(n).padStart(12, "0")}` };
      add("tool.started", {
        tool, ...(detail ? { detail } : {}), call_id: handle.call_id,
        ...(modern ? { tool_call_id: handle.tool_call_id, agent_run_id: "agent-run-1" } : {}),
      });
      return handle;
    },
    finish(handle, { outcome = "completed", ...extra } = {}) {
      add("tool.finished", {
        tool: handle.tool, status: outcome === "completed" ? "ok" : "error", call_id: handle.call_id,
        ...(modern ? { tool_call_id: handle.tool_call_id, agent_run_id: "agent-run-1", outcome, evidence_ids: [], artifact_ids: [] } : {}),
        ...extra,
      });
    },
    /** start + finish back to back. */
    call(tool, detail, options) {
      const handle = api.start(tool, detail, options);
      api.finish(handle, options);
      return handle;
    },
    note(text, epoch) { add("assistant.message", { text, ...(epoch ? { epoch } : {}) }); },
    phase(type, spawn) { add("subagent.started", { type, spawn_id: spawn }); },
    phaseEnd(spawn, status = "COMPLETED") { add("subagent.finished", { spawn_id: spawn, status }); },
    bookkeeping(type = "model.started") { add(type, {}); },
  };
  return api;
}

/** Research: notes, parallel calls of one tool settling out of order, folded reads, a hidden input read, two phases. */
export function researchRun(options) {
  const b = builder(options);
  b.add("task.created"); b.add("task.status", { status: "RUNNING" });
  b.phase("builtin/researcher", "s1"); b.bookkeeping();
  b.note("I'll start by searching for the announcement.", 1);
  b.finish(b.start("web_search", "agent harness benchmarks"));
  b.call("read_task_input", "goal");
  b.note("Now I'll open the strongest results side by side.", 2);
  const a = b.start("fetch_webpage", "a.example.com/post");
  const c = b.start("fetch_webpage", "b.example.com");
  const d = b.start("fetch_webpage", "c.example.com/news");
  b.finish(c); b.finish(a, { outcome: "failed" }); b.finish(d);
  for (let i = 0; i < 4; i++) b.call("read_research_source");
  b.phaseEnd("s1");
  b.phase("builtin/verifier", "s2");
  b.call("read_research_source"); b.call("read_research_source");
  b.phaseEnd("s2");
  b.add("task.finished", { status: "COMPLETED", error_code: null });
  return b.events;
}

/** Coding: workspace, reads, edits, tests that pass the tool call (exit codes only exist in the modern style), a repair, a diff, a build. */
export function codingRun(options) {
  const b = builder(options);
  const modern = (options?.style ?? "legacy") === "modern";
  b.add("task.created"); b.add("sandbox.workspace.created", { sandbox_id: "sb" });
  b.note("Let me look at the repository layout first.", 1);
  b.call("list_repo_files", "src"); b.call("search_repo", "parse_args");
  b.call("read_repo_file", "src/cli.py");
  b.note("I'll change the parser and run the tests.", 2);
  b.call("read_sandbox_file", "src/cli.py");
  b.call("edit_sandbox_file", "src/cli.py");
  b.call("write_sandbox_file", "tests/test_cli.py");
  b.call("run_tests", "python_pytest", modern ? { exit_code: 1, qualified: true } : {});
  b.add("task.repair.started", { attempt: 1 });
  b.call("edit_sandbox_file", "src/cli.py");
  b.call("run_tests", "python_pytest", modern ? { exit_code: 0, qualified: true } : {});
  b.call("git_diff");
  b.call("run_sandbox_command", "node_build", modern ? { exit_code: 0, qualified: true } : {});
  b.call("get_test_results"); b.call("get_test_results");
  b.add("task.finished", { status: "COMPLETED", error_code: null });
  return b.events;
}

/** Failure: failing tools, a phase that fails, the task fails. */
export function failureRun(options) {
  const b = builder(options);
  b.add("task.created");
  b.phase("builtin/researcher", "s1");
  b.finish(b.start("web_search", "latest release"), { outcome: "failed", error_code: "SEARCH_PROVIDER_ERROR" });
  b.finish(b.start("fetch_webpage", "example.com/x"), { outcome: "failed", error_code: "FETCH_NETWORK_ERROR" });
  b.phaseEnd("s1", "FAILED");
  b.phase("builtin/verifier", "s2");
  b.phaseEnd("s2", "BLOCKED");
  b.add("task.finished", { status: "FAILED", error_code: "RESEARCH_FAILED" });
  return b.events;
}

/** Cancellation: one call is stopped (the finish arrives as an error), another never finishes at all. */
export function cancellationRun(options) {
  const b = builder(options);
  b.add("task.created");
  b.note("Searching now.", 1);
  b.finish(b.start("web_search", "q"), { outcome: "cancelled", error_code: "USER_CANCELLED" });
  b.start("fetch_webpage", "slow.example.com");
  b.add("task.cancel_requested", {});
  b.add("task.finished", { status: "CANCELLED", error_code: "USER_CANCELLED" });
  return b.events;
}

/** Legacy events without any call id: only start/finish order says which belongs to which. */
export function legacyNoCallIds() {
  const b = builder();
  const strip = () => { for (const event of b.events) delete event.payload.call_id; };
  b.finish(b.start("web_search", "q1"));
  b.finish(b.start("fetch_webpage", "a.example.com"));
  b.start("fetch_webpage", "b.example.com");          // never finishes
  b.finish(b.start("read_repo_file", "src/a.py"), { outcome: "failed" });
  b.add("tool.finished", { tool: "git_diff", status: "ok" }); // a finish whose start was never seen
  strip();
  return b.events;
}
