"""Tool calls shown live: a call as the model writes it, and a command's output while it runs.

The writer runs against the in-memory `task_streams` of `test_streaming.FakeSessions`; the same SQL is exercised on
PostgreSQL by `examples/phase8_streaming_smoke.py`. Nothing here needs a provider, a VM or a database.
"""
import json
from decimal import Decimal
from types import SimpleNamespace as NS
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from agentforge.contracts.agent_definition import Budget
from agentforge.llm.models import ModelRates
from agentforge.llm.openai_adapter import OpenAIAdapter
from agentforge.orchestration.stream import DRAFT_CHARS, FLUSH_SECONDS, OUTPUT_CHARS, ReplyStream
from agentforge.subagents.registry import AGENT_VERSION, load_builtin
from agentforge.subagents.runtime import ChildRuntime
from agentforge.tools.errors import ToolError
from agentforge.tools.lifecycle import legacy_call_id

from test_child_runtime import FakeGateway, Ledger, ScriptedModel, calls, text
from test_streaming import FakeSessions, FakeStream, chunk
from test_tool_gateway import args, make, ok


def fragment(index, id=None, name=None, arguments=None):
    return [NS(index=index, id=id, function=NS(name=name, arguments=arguments))]


# --- the provider adapter ---------------------------------------------------------------------------------------
class AdapterToolTests(IsolatedAsyncioTestCase):
    def adapter(self, stream):
        adapter = OpenAIAdapter(api_key="k", model_id="m", rates=ModelRates(Decimal(1), Decimal(2)),
            context_window_tokens=100_000, max_output_tokens=1000)

        async def create(**kwargs):
            self.kwargs = kwargs
            return stream
        adapter._client = NS(chat=NS(completions=NS(create=create)))
        return adapter

    TOOLS = [{"type": "function", "function": {"name": "write", "parameters": {"type": "object"}}}]

    async def test_each_piece_of_a_call_is_reported_as_it_arrives_with_the_id_and_name_so_far(self):
        stream = FakeStream([chunk("I'll write it."), chunk(calls=fragment(0, "c1", "write", '{"path": "a')),
                             chunk(calls=fragment(0, None, None, '.py"}')), chunk(calls=fragment(1, "c2", "read", "{}")),
                             chunk(finish="tool_calls")])
        seen, notes = [], []
        response = await self.adapter(stream).complete([{"role": "user", "content": "x"}], self.TOOLS,
            on_narration=notes.append, on_tool=lambda *piece: seen.append(piece))
        self.assertEqual(seen, [(0, "c1", "write", '{"path": "a'), (0, "c1", "write", '.py"}'), (1, "c2", "read", "{}")])
        self.assertEqual(notes, ["I'll write it."])
        # What is reported never changes what is returned.
        self.assertEqual([call["function"] for call in response.message["tool_calls"]],
                         [{"name": "write", "arguments": '{"path": "a.py"}'}, {"name": "read", "arguments": "{}"}])

    async def test_reporting_calls_alone_is_enough_to_stream(self):
        stream = FakeStream([chunk(calls=fragment(0, "c1", "write", "{}")), chunk(finish="tool_calls")])
        await self.adapter(stream).complete([], self.TOOLS, on_tool=lambda *piece: None)
        self.assertTrue(self.kwargs["stream"])

    async def test_a_failing_callback_never_fails_the_call(self):
        def broken(*piece):
            raise RuntimeError("ui failure")
        stream = FakeStream([chunk(calls=fragment(0, "c1", "write", "{}")), chunk(finish="tool_calls")])
        response = await self.adapter(stream).complete([], self.TOOLS, on_tool=broken)
        self.assertEqual(response.message["tool_calls"][0]["id"], "c1")

    async def test_a_note_then_a_call_reach_the_live_row_in_order(self):
        sessions, events, now = FakeSessions(), [], [0.0]
        live = ReplyStream(sessions, "task", emit=lambda kind, payload: events.append((kind, payload, dict(sessions.row or {}))), clock=lambda: now[0])
        arguments = json.dumps({"path": "src/a.py", "content": "print(1)\n"})
        stream = FakeStream([chunk("I'll create the file."), chunk(calls=fragment(0, "c1", "write", arguments[:20])),
                             chunk(calls=fragment(0, None, None, arguments[20:])), chunk(finish="tool_calls")])
        live.begin()
        await self.adapter(stream).complete([], self.TOOLS, on_narration=live.feed_narration, on_tool=live.feed_tool)
        live.end()
        # The note was saved while it was still the live text; the call then opened its own epoch, after it.
        self.assertEqual([(kind, payload) for kind, payload, _ in events], [("assistant.message", {"text": "I'll create the file.", "epoch": 1})])
        self.assertEqual(events[0][2]["kind"], "progress")
        self.assertEqual(sessions.row, {"epoch": 2, "revision": 1, "kind": "tool", "text": "print(1)\n", "tool": "write",
                                        "call_id": "c1", "detail": "src/a.py"})


# --- the live row ----------------------------------------------------------------------------------------------
class DraftTests(TestCase):
    def stream(self, emit=True):
        self.now, self.events = 0.0, []
        self.sessions = FakeSessions()
        sink = (lambda kind, payload: self.events.append((kind, payload))) if emit else None
        return ReplyStream(self.sessions, "task", emit=sink, clock=lambda: self.now)

    def row(self):
        return self.sessions.row

    def test_a_call_is_shown_the_moment_it_is_named_even_before_it_has_any_text(self):
        stream = self.stream()
        stream.begin()
        stream.feed_tool(0, "run:tool:c1", "bash", "")
        self.assertEqual(self.row(), {"epoch": 1, "revision": 0, "kind": "tool", "text": "", "tool": "bash", "call_id": "run:tool:c1"})

    def test_its_text_grows_as_it_is_written_and_says_what_it_is_about(self):
        stream = self.stream()
        stream.begin()
        arguments = json.dumps({"path": "src/a.py", "content": "print(1)\nprint(2)\n"})
        stream.feed_tool(0, "c1", "write", arguments[:30])
        self.assertEqual((self.row()["text"], self.row()["detail"]), ("", "src/a.py"))
        for start in (30, 40):
            self.now += FLUSH_SECONDS
            stream.feed_tool(0, "c1", "write", arguments[start:start + 10])
        stream.feed_tool(0, "c1", "write", arguments[50:])
        stream.end()
        self.assertEqual(self.row(), {"epoch": 1, "revision": 0, "kind": "tool", "text": "print(1)\nprint(2)\n", "tool": "write",
                                      "call_id": "c1", "detail": "src/a.py"})  # growth is an append: the revision never moved

    def test_text_is_decoded_whole_characters_only_and_is_always_the_start_of_the_final_text(self):
        command = 'echo "héllo 👍"\nls \\ done'
        arguments = json.dumps({"command": command})
        stream = self.stream()
        stream.begin()
        for position in range(0, len(arguments), 3):
            self.now += FLUSH_SECONDS
            stream.feed_tool(0, "c1", "bash", arguments[position:position + 3])
            self.assertTrue(command.startswith(self.row()["text"]), self.row()["text"])
        self.assertEqual(self.row()["text"], command)

    def test_the_note_before_a_call_is_saved_first_and_the_call_opens_a_new_epoch_after_it(self):
        stream = self.stream()
        stream.begin()
        stream.feed_narration("I'll run it.")
        stream.feed_tool(0, "c1", "bash", '{"command": "ls')
        self.assertEqual(self.events, [("assistant.message", {"text": "I'll run it.", "epoch": 1})])
        self.assertEqual([(row["epoch"], row["kind"]) for row in self.sessions.history], [(1, "progress"), (2, "tool")])
        self.assertEqual(self.row()["text"], "ls")
        stream.end()
        stream.commit_narration()  # the runtime's own commit after the call: nothing is left to save or clear
        self.assertEqual(len(self.events), 1)
        self.assertEqual(self.row()["kind"], "tool")

    def test_calls_with_short_arguments_are_not_shown_until_they_run(self):
        stream = self.stream()
        stream.begin()
        stream.feed_tool(0, "c1", "read_repo_file", '{"path": "a.py"}')
        stream.feed_tool(1, "c2", "web_search", "")
        stream.feed_tool(2, "", "bash", '{"command": "ls"}')  # not named by the provider yet
        self.assertEqual(self.sessions.writes, [])

    def test_a_second_call_takes_the_place_of_the_first(self):
        stream = self.stream()
        stream.begin()
        stream.feed_tool(0, "a", "write", json.dumps({"path": "a.py", "content": "one"}))
        stream.feed_tool(1, "b", "bash", '{"command": "make"}')
        self.assertEqual(self.row(), {"epoch": 1, "revision": 1, "kind": "tool", "text": "make", "tool": "bash", "call_id": "b"})

    def test_a_long_call_shows_its_start_only(self):
        stream = self.stream()
        stream.begin()
        stream.feed_tool(0, "c1", "write", json.dumps({"path": "big.txt", "content": "x" * (3 * DRAFT_CHARS)}))
        self.assertEqual(len(self.row()["text"]), DRAFT_CHARS)

    def test_malformed_arguments_show_nothing_and_never_fail(self):
        stream = self.stream()
        stream.begin()
        stream.feed_tool(0, "c1", "write", "not json at all {{{")
        self.assertEqual((self.row()["kind"], self.row()["text"]), ("tool", ""))

    # --- what ends it -------------------------------------------------------------------------
    def test_settling_clears_the_call_and_only_the_call_that_is_shown(self):
        stream = self.stream()
        stream.begin()
        stream.feed_tool(0, "a", "bash", '{"command": "ls"}')
        stream.settle("other")
        self.assertEqual(self.row()["kind"], "tool")
        stream.settle("a")
        self.assertEqual(self.row(), {"epoch": 1, "revision": 1, "kind": "progress", "text": ""})
        writes = len(self.sessions.writes)
        stream.settle("a")
        stream.settle()
        self.assertEqual(len(self.sessions.writes), writes)  # nothing left to clear

    def test_a_new_attempt_or_a_discard_clears_a_call_that_is_still_shown(self):
        for ending in ("begin", "discard"):
            stream = self.stream()
            stream.begin()
            stream.feed_tool(0, "a", "bash", '{"command": "ls"}')
            getattr(stream, ending)()
            self.assertEqual((self.row()["kind"], self.row()["text"]), ("progress", ""), ending)
            self.assertNotIn("tool", self.row())

    def test_the_end_of_the_task_clears_the_call_too(self):
        from agentforge.orchestration.stream import clear_stream
        stream = self.stream()
        stream.begin()
        stream.feed_tool(0, "a", "bash", '{"command": "ls"}')
        clear_stream(self.sessions, "task")
        self.assertEqual(self.row(), {"epoch": 1, "revision": 1, "kind": "progress", "text": ""})

    def test_a_note_that_cannot_be_saved_fails_the_run_when_the_call_ends_not_in_the_middle_of_the_stream(self):
        stream = self.stream()
        failure = RuntimeError("lease lost")

        def refuse(kind, payload):
            raise failure
        stream.emit = refuse
        stream.begin()
        stream.feed_narration("I'll run it.")
        stream.feed_tool(0, "c1", "bash", '{"command": "ls"}')  # a model's sink swallows what it raises: nothing is raised here
        with self.assertRaises(RuntimeError) as raised:
            stream.end()
        self.assertIs(raised.exception, failure)
        stream.end()  # raised once

    def test_a_failing_database_switches_streaming_off_without_failing_anything(self):
        stream = ReplyStream(FakeSessions(fail=True), "task", clock=lambda: 0.0)
        stream.begin()
        stream.feed_tool(0, "a", "bash", '{"command": "ls"}')
        stream.output("a", "bash")("line\n")
        stream.settle()
        self.assertTrue(stream.disabled)


class OutputTests(TestCase):
    def stream(self):
        self.now = 0.0
        self.sessions = FakeSessions()
        stream = ReplyStream(self.sessions, "task", clock=lambda: self.now)
        stream.begin()
        return stream

    def row(self):
        return self.sessions.row

    def test_a_running_call_opens_its_own_epoch_over_the_call_that_was_written(self):
        stream = self.stream()
        stream.feed_tool(0, "a", "bash", '{"command": "ls"}')
        stream.output("a", "bash")
        self.assertEqual(self.row(), {"epoch": 2, "revision": 1, "kind": "output", "text": "", "tool": "bash", "call_id": "a"})

    def test_output_grows_by_appending(self):
        stream = self.stream()
        sink = stream.output("a", "bash")
        sink("one\n")
        self.now += FLUSH_SECONDS
        sink("two\n")
        self.assertEqual(self.row(), {"epoch": 1, "revision": 0, "kind": "output", "text": "one\ntwo\n", "tool": "bash", "call_id": "a"})

    def test_output_is_throttled_like_any_live_text(self):
        stream = self.stream()
        sink = stream.output("a", "bash")
        writes = len(self.sessions.writes)
        for number in range(50):
            sink(f"line {number}\n")
        self.assertEqual(len(self.sessions.writes), writes)  # all inside one window
        self.now += FLUSH_SECONDS
        sink("last\n")
        self.assertTrue(self.row()["text"].endswith("line 49\nlast\n"))

    def test_credentials_in_output_are_redacted_before_anything_is_published(self):
        stream = self.stream()
        sink = stream.output("a", "bash")
        token = "ghp_" + "a" * 30
        self.now += FLUSH_SECONDS
        sink(f"token: {token}\n-----BEGIN RSA PRIVATE KEY-----\nMIIsecret")
        self.assertNotIn(token, self.row()["text"])
        self.assertNotIn("MIIsecret", self.row()["text"])
        self.assertIn("[REDACTED", self.row()["text"])

    def test_a_long_run_shows_the_end_of_its_output_in_whole_lines(self):
        stream = self.stream()
        sink = stream.output("a", "bash")
        line = "x" * 49 + "\n"
        for _ in range(400):
            self.now += FLUSH_SECONDS
            sink(line)
        shown = self.row()["text"]
        self.assertTrue(shown.startswith("…\n"))
        self.assertLessEqual(len(shown), OUTPUT_CHARS + 2)
        self.assertEqual(set(shown[2:].split("\n")), {"x" * 49, ""})  # no line is cut in half
        self.assertGreater(self.row()["revision"], 0)  # the window moved: it is a replacement, not an append

    def test_output_that_arrives_after_the_call_settled_is_ignored(self):
        stream = self.stream()
        sink = stream.output("a", "bash")
        stream.settle("a")
        writes = len(self.sessions.writes)
        self.now += FLUSH_SECONDS
        sink("late\n")
        self.assertEqual(len(self.sessions.writes), writes)
        self.assertEqual((self.row()["kind"], self.row()["text"]), ("progress", ""))

    def test_a_stale_writer_cannot_bring_output_back(self):
        stream = self.stream()
        sink = stream.output("a", "bash")
        newer = ReplyStream(self.sessions, "task", clock=lambda: self.now)
        newer.begin()
        newer.feed("The next call's words")
        self.now += FLUSH_SECONDS
        sink("old output\n")
        self.assertEqual(self.row()["text"], "The next call's words")


# --- the gateway ------------------------------------------------------------------------------------------------
class FakeLive:
    """What the gateway needs of the run's live view, recording how it is used."""

    def __init__(self, calls_made=None):
        self.log, self.chunks, self.calls_made = [], [], calls_made

    def output(self, call_id, tool):
        self.log.append(("output", call_id, tool))
        return self.chunks.append

    def settle(self, call_id=None):
        self.log.append(("settle", call_id, len(self.calls_made.outcomes) if self.calls_made else None))


class GatewayLiveTests(IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = patch("agentforge.tools.gateway.authorize_tool")
        patcher.start()
        self.addCleanup(patcher.stop)

    def gateway(self, bash):
        gateway, saved, _ = make()
        gateway.catalog.register("bash", bash, max_cost_usd=Decimal(0), timeout_sec=5)
        gateway.scope = NS(allowed_tools=(*gateway.scope.allowed_tools, "bash"), permissions=NS(), budget=Budget())
        gateway.live = FakeLive(saved)
        return gateway, saved

    def call_id(self, gateway, provider_id):
        return legacy_call_id(f"{gateway.run_id}:tool:{provider_id}")

    async def test_a_commands_output_goes_to_the_live_view_while_it_runs_and_is_cleared_after_its_outcome_is_saved(self):
        async def bash(arguments, execution):
            execution.output("one\n")
            execution.output("two\n")
            return ok(exit_code=0, output="one\ntwo\n")
        gateway, _ = self.gateway(bash)
        outcome = await gateway.execute("bash", args(command="ls"), "c1")
        self.assertEqual(outcome.status, "ok")
        call = self.call_id(gateway, "c1")
        self.assertEqual(gateway.live.chunks, ["one\n", "two\n"])
        # The row is told to show output once, and cleared once, only after the call's outcome was saved.
        self.assertEqual(gateway.live.log, [("output", call, "bash"), ("settle", call, 1)])

    async def test_a_command_that_fails_still_clears_its_output(self):
        async def bash(arguments, execution):
            execution.output("partial\n")
            raise ToolError("SANDBOX_DISCONNECTED", "dropped")
        gateway, _ = self.gateway(bash)
        outcome = await gateway.execute("bash", args(command="ls"), "c1")
        self.assertEqual(outcome.error_code, "SANDBOX_DISCONNECTED")
        self.assertEqual(gateway.live.log[-1][0], "settle")

    async def test_other_tools_get_no_output_sink_and_replace_whatever_showed_them_being_written(self):
        seen = []

        async def read(arguments, execution):
            seen.append(execution.output)
            return ok(content="x")
        gateway, _, _ = make({"read": read})
        gateway.live = FakeLive()
        await gateway.execute("read", args(path="a.py"), "c1")
        call = self.call_id(gateway, "c1")
        self.assertEqual(seen, [None])
        self.assertEqual([entry[:2] for entry in gateway.live.log], [("settle", call), ("settle", call)])

    async def test_a_call_that_is_refused_before_it_runs_clears_what_showed_it_being_written(self):
        gateway, _ = self.gateway(AsyncMock())
        outcome = await gateway.execute("bash", args(), "c1")  # no command
        self.assertEqual(outcome.error_code, "SCHEMA_INVALID")
        self.assertEqual([entry[:2] for entry in gateway.live.log], [("settle", self.call_id(gateway, "c1"))])

    async def test_without_a_live_view_nothing_changes(self):
        seen = []

        async def bash(arguments, execution):
            seen.append(execution.output)
            return ok(exit_code=0)
        gateway, _ = self.gateway(bash)
        gateway.live = None
        await gateway.execute("bash", args(command="ls"), "c1")
        self.assertEqual(seen, [None])


# --- the whole pipeline ----------------------------------------------------------------------------------------
class PipelineTests(IsolatedAsyncioTestCase):
    """The real adapter, loop, gateway and live row together, with a provider that streams and a command that prints.

    The browser matches the call it drew to the row that replaces it by the id both carry, so the two ids must be the
    same string, computed independently by the loop (while the call is written) and by the gateway (when it starts).
    """

    def setUp(self):
        patcher = patch("agentforge.tools.gateway.authorize_tool")
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_a_note_then_a_command_is_drawn_then_runs_with_its_output_then_settles_under_one_id(self):
        events, now = [], [0.0]
        sessions = FakeSessions()
        live = ReplyStream(sessions, "task", emit=lambda kind, payload: events.append((kind, payload)), clock=lambda: now[0])

        async def bash(arguments, execution):
            now[0] += FLUSH_SECONDS
            execution.output("compiling\n")
            now[0] += FLUSH_SECONDS
            execution.output("done\n")
            return ok(exit_code=0, output="compiling\ndone\n", timed_out=False, truncated=False)
        gateway, saved, _ = make()
        gateway.catalog.register("bash", bash, max_cost_usd=Decimal(0), timeout_sec=5)
        gateway.scope = NS(allowed_tools=(*gateway.scope.allowed_tools, "bash"), permissions=NS(), budget=Budget())
        gateway.live, gateway.lifecycle = live, lambda kind, payload: events.append((kind, payload))

        arguments = json.dumps({"command": "make build"})
        turns = [
            [chunk("I'll build it."), chunk(calls=fragment(0, "call_9", "bash", arguments[:9])),
             chunk(calls=fragment(0, None, None, arguments[9:])), chunk(finish="tool_calls")],
            [chunk("Built."), chunk(finish="stop")],
        ]
        adapter = OpenAIAdapter(api_key="k", model_id="m", rates=ModelRates(Decimal(1), Decimal(2)), context_window_tokens=100_000, max_output_tokens=1000)

        async def create(**kwargs):
            return FakeStream(turns.pop(0))
        adapter._client = NS(chat=NS(completions=NS(create=create)))

        result = await ChildRuntime().run(
            definition=load_builtin("agent", AGENT_VERSION), ctx=gateway.ctx, scope=gateway.scope, model=adapter, gateway=gateway,
            budgets=Ledger(), reservation=NS(id=uuid4(), run_id=gateway.run_id),
            messages=[{"role": "developer", "content": "rules"}, {"role": "user", "content": "build it"}],
            resources=NS(), reauthorize=AsyncMock(), stream=live)
        self.assertEqual(result.summary, "Built.")

        # The order the person sees: the note, the call as it was written, the same call's own row, its output, then nothing.
        kinds = [event[0] for event in events]
        self.assertEqual([kind for kind in kinds if kind in ("assistant.message", "tool.started", "tool.finished")],
                         ["assistant.message", "tool.started", "tool.finished"])
        started = next(payload for kind, payload in events if kind == "tool.started")
        shown = [(row["kind"], row.get("call_id")) for row in sessions.history]
        self.assertIn(("tool", started["call_id"]), shown)
        self.assertIn(("output", started["call_id"]), shown)
        self.assertLess(shown.index(("tool", started["call_id"])), shown.index(("output", started["call_id"])))
        drawn = [row for row in sessions.history if row["kind"] == "tool"]
        self.assertEqual((drawn[0]["text"], drawn[-1]["tool"], drawn[-1]["text"]), ("", "bash", "make build"))  # named first, then written
        printed = [row["text"] for row in sessions.history if row["kind"] == "output"]
        self.assertEqual(printed[-1], "compiling\ndone\n")
        # The call's own row replaced the drawing for good, it settled once its outcome was saved, and the answer that
        # followed was shown as an answer with nothing of the call left on the row.
        order = [row["kind"] for row in sessions.history]
        self.assertNotIn("tool", order[order.index("output"):])
        self.assertEqual(saved.last()["status"], "SUCCEEDED")
        self.assertEqual(sessions.row, {"epoch": sessions.row["epoch"], "revision": sessions.row["revision"], "kind": "answer", "text": "Built."})


# --- the loop --------------------------------------------------------------------------------------------------
class StreamingModel(ScriptedModel):
    """A scripted model that, like the real adapter, reports each tool call piece by piece as it writes it."""

    async def complete(self, messages, tools, on_text=None, tool_choice=None, on_narration=None, on_tool=None):
        turn = self.turns[0]
        if on_tool is not None and isinstance(turn, dict):
            for index, call in enumerate(turn.get("tool_calls") or []):
                arguments = call["function"]["arguments"]
                on_tool(index, call["id"], call["function"]["name"], arguments[:7])
                on_tool(index, call["id"], call["function"]["name"], arguments[7:])
        return await super().complete(messages, tools, on_text, tool_choice, on_narration)


class RecordingStream:
    def __init__(self, drafts=True):
        self.log = []
        if drafts:
            self.feed_tool = lambda *piece: self.log.append(("tool", *piece))

    def begin(self): self.log.append(("begin",))
    def feed(self, chunk): self.log.append(("text", chunk))
    feed_narration = feed
    def end(self): self.log.append(("end",))
    def commit_narration(self): self.log.append(("commit_narration",))
    def commit_reply(self): self.log.append(("commit_reply",))
    def discard(self): self.log.append(("discard",))


class RuntimeToolStreamTests(IsolatedAsyncioTestCase):
    async def run_loop(self, model, stream):
        self.reservation = NS(id=uuid4(), run_id=uuid4())
        return await ChildRuntime().run(
            definition=load_builtin("agent", AGENT_VERSION), ctx=NS(task_id=uuid4()),
            scope=NS(budget=Budget(), allowed_tools=("read_repo_file",)), model=model, gateway=FakeGateway(),
            budgets=Ledger(), reservation=self.reservation,
            messages=[{"role": "developer", "content": "rules"}, {"role": "user", "content": "go"}],
            resources=NS(), reauthorize=AsyncMock(), stream=stream)

    async def test_a_call_is_reported_as_it_is_written_under_the_id_its_start_event_will_carry(self):
        stream = RecordingStream()
        model = StreamingModel([calls(("write", {"path": "a.py", "content": "x"}), content="I'll write it."), text("Done.")])
        result = await self.run_loop(model, stream)
        self.assertEqual(result.summary, "Done.")
        pieces = [entry for entry in stream.log if entry[0] == "tool"]
        self.assertEqual([entry[1] for entry in pieces], [0, 0])
        self.assertEqual({entry[2] for entry in pieces}, {legacy_call_id(f"{self.reservation.run_id}:tool:call-0-write")})
        self.assertEqual({entry[3] for entry in pieces}, {"write"})
        self.assertEqual("".join(entry[4] for entry in pieces), json.dumps({"path": "a.py", "content": "x"}))

    async def test_a_stream_that_cannot_show_calls_is_simply_not_asked_to(self):
        stream = RecordingStream(drafts=False)  # a delegated child's stream keeps notes only
        model = StreamingModel([calls(("write", {"path": "a.py", "content": "x"})), text("Done.")])
        result = await self.run_loop(model, stream)
        self.assertEqual(result.summary, "Done.")
        self.assertFalse([entry for entry in stream.log if entry[0] == "tool"])

    async def test_a_model_that_does_not_report_calls_works_as_before(self):
        result = await self.run_loop(ScriptedModel([calls(("read_repo_file", {"path": "a"})), text("Done.")]), RecordingStream())
        self.assertEqual(result.summary, "Done.")

    async def test_drafts_reach_the_stream_through_the_traced_model_the_worker_uses(self):
        # bootstrap.py always wraps the model in ObservedModel: the wrapper must not hide that the model streams calls.
        from contextlib import contextmanager
        from agentforge.telemetry.runtime import ObservedModel

        @contextmanager
        def span(*args, **kwargs):
            yield None
        stream = RecordingStream()
        inner = StreamingModel([calls(("write", {"path": "a.py", "content": "x"})), text("Done.")])
        observed = ObservedModel(inner, NS(span=span), NS(progress=lambda *a: None, lease=NS(task_id="t")))
        result = await self.run_loop(observed, stream)
        self.assertEqual(result.summary, "Done.")
        self.assertTrue([entry for entry in stream.log if entry[0] == "tool"])

    async def test_an_event_error_raised_as_the_call_ends_still_records_the_call_at_its_real_cost(self):
        class Failing(RecordingStream):
            def end(self):
                super().end()
                raise RuntimeError("lease lost")
        stream, ledger = Failing(), Ledger()
        with self.assertRaises(RuntimeError):
            await ChildRuntime().run(
                definition=load_builtin("agent", AGENT_VERSION), ctx=NS(task_id=uuid4()),
                scope=NS(budget=Budget(), allowed_tools=("read_repo_file",)), model=StreamingModel([text("Done.")]),
                gateway=FakeGateway(), budgets=ledger, reservation=NS(id=uuid4(), run_id=uuid4()),
                messages=[{"role": "user", "content": "go"}], resources=NS(), reauthorize=AsyncMock(), stream=stream)
        # One entry for the call, with what the provider reported: the upper bound is never charged for a call that worked.
        self.assertEqual([(cost, usage["output_tokens"]) for _key, cost, usage in ledger.usage], [(Decimal("0.0001"), 1)])
        self.assertIn(("discard",), stream.log)
        self.assertNotIn(("commit_reply",), stream.log)
