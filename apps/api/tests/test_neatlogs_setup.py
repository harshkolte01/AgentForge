"""Neatlogs setup: what the export mask keeps and drops, how the SDK is configured, and that tracing can never stop a run."""
import asyncio
import logging
import unittest
from contextlib import contextmanager
from types import SimpleNamespace as NS
from unittest.mock import patch

from pydantic import SecretStr, ValidationError

from agentforge.settings import Settings
from agentforge.telemetry import neatlogs_setup
from agentforge.telemetry.neatlogs_setup import (
    Telemetry, annotate, content_mask, current_trace_id, init_options, mark_error, metadata_only, parse_tags, redact, stage)

KEY = SecretStr("nl_test_key_for_unit_tests")


def settings(**overrides):
    return Settings(_env_file=None, app_env="test", neatlogs_api_key=KEY, **overrides)


def untraced():
    return Telemetry(Settings(_env_file=None, app_env="test"))


def snapshot(**attributes):
    return {"signal": "span", "name": "model_request", "attributes": attributes, "events": [], "links": [],
            "resource": {"attributes": {"service.name": "svc", "process.command_line": "python run.py --secret"}},
            "status": {"code": "ERROR", "description": "Traceback with /home/me/private.py"}}


class MetadataMaskTests(unittest.TestCase):
    def test_what_the_dashboard_reads_is_kept(self):
        wanted = {
            "neatlogs.span.kind": "llm", "neatlogs.llm.token_count.prompt": 10, "neatlogs.llm.token_count.completion": 5,
            "neatlogs.llm.token_count.total": 15, "neatlogs.llm.cost_usd": 0.01, "neatlogs.llm.model_name": "m",
            "neatlogs.llm.provider": "openai", "neatlogs.llm.choices.0.finish_reason": "stop", "neatlogs.session.id": "s1",
            "neatlogs.end_user.id": "u1", "neatlogs.tags": "env:test", "neatlogs.metrics.duration_ms": 4.2,
            "neatlogs.tool.name": "bash", "neatlogs.guardrail.passed": False, "neatlogs.guardrail.name": "tool_approval",
            "neatlogs.langgraph.node": "work", "agentforge.error.code": "TOOL_FAILED", "agentforge.task.status": "FAILED"}
        self.assertEqual(metadata_only(snapshot(**wanted))["attributes"], wanted)

    def test_prompts_answers_tool_data_urls_and_unknown_attributes_are_dropped(self):
        kept = metadata_only(snapshot(**{
            "input.value": "secret goal", "output.value": "secret answer", "neatlogs.chain.input": "state",
            "neatlogs.llm.input_messages.0.content": "private", "neatlogs.llm.output_messages.0.content": "private",
            "llm.tool_calls.0.arguments": "{}", "neatlogs.guardrail.input": "text", "neatlogs.guardrail.output": "text",
            "http.url": "https://private.example/x", "some.other": "x", "neatlogs.llm.token_count.total": 3}))
        self.assertEqual(kept["attributes"], {"neatlogs.llm.token_count.total": 3})

    def test_only_scalar_values_survive(self):
        kept = metadata_only(snapshot(**{"agentforge.x": ["a"], "agentforge.y": {"a": 1}, "agentforge.z": 1}))
        self.assertEqual(kept["attributes"], {"agentforge.z": 1})

    def test_the_status_keeps_its_code_but_never_its_text(self):
        kept = metadata_only(snapshot())
        self.assertEqual(kept["status"], {"code": "ERROR", "description": None})
        self.assertEqual(metadata_only({"status": {"code": "weird"}})["status"]["code"], "UNSET")

    def test_an_exception_event_keeps_only_its_kind(self):
        span = snapshot()
        span["events"] = [{"name": "exception", "timestamp": 5, "attributes": {"exception.type": "ToolError",
            "exception.message": "private path /x", "exception.stacktrace": "Traceback ...", "exception.escaped": "False"}},
            {"name": "log", "timestamp": 6, "attributes": {"text": "private"}}]
        kept = metadata_only(span)
        self.assertEqual(kept["events"], [{"name": "exception", "timestamp": 5,
            "attributes": {"exception.type": "ToolError", "exception.escaped": "False"}}])
        self.assertEqual(kept["links"], [])

    def test_a_failures_code_is_shown_as_its_exception_type(self):
        span = snapshot(**{"agentforge.error.code": "RATE_LIMIT"})
        span["events"] = [{"name": "exception", "timestamp": 5, "attributes": {"exception.type": "agentforge.llm.models.ModelProviderError",
            "exception.message": "private"}}]
        self.assertEqual(metadata_only(span)["events"][0]["attributes"], {"exception.type": "RATE_LIMIT"})
        self.assertEqual(content_mask()(span)["events"][0]["attributes"], {"exception.type": "RATE_LIMIT", "exception.message": "private"})

    def test_only_known_resource_attributes_are_sent(self):
        self.assertEqual(metadata_only(snapshot())["resource"], {"attributes": {"service.name": "svc"}})

    def test_a_malformed_span_is_reduced_not_crashed_on(self):
        kept = metadata_only({"attributes": "x", "events": "y", "resource": 3, "status": None})
        self.assertEqual((kept["attributes"], kept["events"], kept["resource"]["attributes"]), ({}, [], {}))


class ContentMaskTests(unittest.TestCase):
    def test_secret_shapes_are_masked_wherever_they_appear(self):
        # Assembled here so that no complete token-shaped literal sits in the repository (secret scanners match those).
        shapes = ["sk" + "-proj-abcdefghijklmnopqrstuv", "gh" + "p_abcdefghijklmnopqrstuvwxyz0123", "github" + "_pat_11ABCDEFG0123456789abcdef",
            "AK" + "IAABCDEFGHIJKLMNOP", "xox" + "b-1234567890-abcdefghij", "tvly" + "-abcdefghijklmnop",
            "ey" + "JhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJlMTIzNDU2",
            "Bearer " + "abcdefghijklmnop1234"]
        for shape in shapes:
            self.assertNotIn(shape, redact(f"before {shape} after"), shape)
        self.assertEqual(redact("before " + shapes[0] + " after"), "before [redacted] after")
        self.assertNotIn("hunter22", redact('{"password": "hunter22"}'))
        self.assertNotIn("hunter22", redact("api_key=hunter22abc"))
        self.assertNotIn("p4ss", redact("postgresql://agentforge:p4ssw0rd@db:5432/x"))
        self.assertIn("postgresql://", redact("postgresql://agentforge:p4ssw0rd@db:5432/x"))
        key = "-----BEGIN RSA " + "PRIVATE KEY-----\nMIIabc\n-----END RSA " + "PRIVATE KEY-----"
        self.assertEqual(redact(f"k: {key}"), "k: [redacted]")

    def test_ordinary_text_is_untouched(self):
        text = "Refactor src/app/main.py so the token count is read once; see https://example.com/docs."
        self.assertEqual(redact(text), text)

    def test_configured_secrets_are_masked_even_when_they_have_no_known_shape(self):
        kept = content_mask(("unusual-secret-value-123",))(snapshot(**{"input.value": "use unusual-secret-value-123 now"}))
        self.assertEqual(kept["attributes"]["input.value"], "use [redacted] now")

    def test_short_configured_values_are_not_treated_as_secrets(self):
        kept = content_mask(("abc",))(snapshot(**{"input.value": "abc def"}))
        self.assertEqual(kept["attributes"]["input.value"], "abc def")

    def test_long_values_are_cut_and_status_text_and_stacks_still_never_leave(self):
        span = snapshot(**{"input.value": "x" * 20000})
        span["events"] = [{"name": "exception", "timestamp": 1, "attributes": {"exception.type": "E", "exception.message": "m",
            "exception.stacktrace": "Traceback"}}]
        kept = content_mask()(span)
        self.assertLess(len(kept["attributes"]["input.value"]), 6100)
        self.assertTrue(kept["attributes"]["input.value"].endswith("[truncated]"))
        self.assertIsNone(kept["status"]["description"])
        self.assertEqual(kept["events"][0]["attributes"], {"exception.type": "E", "exception.message": "m"})
        self.assertEqual(kept["resource"], {"attributes": {"service.name": "svc"}})

    def test_every_secret_setting_is_collected(self):
        values = neatlogs_setup.configured_secrets(settings(llm_api_key=SecretStr("llm-key-value-12345"),
            tavily_api_key=SecretStr("tavily-key-value-12345")))
        self.assertTrue({"llm-key-value-12345", "tavily-key-value-12345", "nl_test_key_for_unit_tests"} <= set(values))


class InitOptionsTests(unittest.TestCase):
    def test_defaults(self):
        options = init_options(settings(llm_model="gpt-x"))
        self.assertEqual(options["api_key"], "nl_test_key_for_unit_tests")
        self.assertEqual((options["workflow_name"], options["sample_rate"], options["capture_logs"]), ("agentforge-ai", 1.0, False))
        self.assertEqual(options["user_id"], "agentforge-worker")
        self.assertFalse(options["register_shutdown_handlers"])
        self.assertIs(options["mask"], metadata_only)
        self.assertNotIn("endpoint", options)
        self.assertEqual(options["tags"][0], "env:test")
        self.assertIn("model:gpt-x", options["tags"])

    def test_tags_endpoint_sampling_and_content_mode_follow_the_settings(self):
        options = init_options(settings(neatlogs_tags=" baseline, prompt-v3 ,baseline,,env:test ",
            neatlogs_endpoint="https://eu.ingest.neatlogs.com/", neatlogs_sample_rate=0.5, neatlogs_capture_content=True))
        self.assertEqual(options["endpoint"], "https://eu.ingest.neatlogs.com")
        self.assertEqual(options["sample_rate"], 0.5)
        self.assertEqual([tag for tag in options["tags"] if not tag.startswith(("release:", "model:"))], ["env:test", "baseline", "prompt-v3"])
        self.assertIsNot(options["mask"], metadata_only)

    def test_tags_are_few_and_short(self):
        self.assertEqual(parse_tags(",".join(f"t{n}" for n in range(30))), [f"t{n}" for n in range(10)])
        self.assertEqual(parse_tags("a" * 200), ["a" * 64])
        self.assertEqual(parse_tags(None), [])

    def test_an_endpoint_with_a_path_is_refused_with_the_field_named(self):
        with self.assertRaises(ValidationError) as caught:
            settings(neatlogs_endpoint="https://ingest.example.com/v1/traces")
        self.assertIn("neatlogs_endpoint", str(caught.exception))
        settings(neatlogs_endpoint="https://ingest.example.com")
        settings(neatlogs_endpoint="https://ingest.example.com/")

    def test_the_sample_rate_must_be_a_share(self):
        for bad in (-0.1, 1.5):
            with self.assertRaises(ValidationError):
                settings(neatlogs_sample_rate=bad)


class TelemetryTests(unittest.TestCase):
    def test_without_a_key_everything_is_a_no_op(self):
        telemetry = untraced()
        self.assertFalse(telemetry.enabled)
        self.assertEqual(telemetry.callbacks(), [])
        with telemetry.span("x", kind="TOOL") as span:
            self.assertIsNone(span)
        function = lambda: 1  # noqa: E731
        self.assertIs(telemetry.decorate(function, kind="TOOL", name="x"), function)
        with stage("phase") as span:
            self.assertIsNone(span)
        annotate({"a": 1}, failed="X")
        self.assertIsNone(current_trace_id())
        telemetry.shutdown()

    def test_a_sdk_that_cannot_start_leaves_the_run_untraced_instead_of_stopping_the_worker(self):
        import neatlogs
        with patch.object(neatlogs, "init", side_effect=RuntimeError("endpoint unreachable")), \
                self.assertLogs("agentforge.telemetry.neatlogs_setup", level=logging.ERROR) as logs:
            telemetry = Telemetry(settings())
        self.assertFalse(telemetry.enabled)
        self.assertEqual(telemetry.callbacks(), [])
        self.assertNotIn("nl_test_key_for_unit_tests", "\n".join(logs.output))
        with telemetry.span("x") as span:
            self.assertIsNone(span)
        telemetry.shutdown()

    def test_shutdown_flushes_then_closes_with_a_bound_and_never_raises(self):
        calls = []
        telemetry = untraced()
        telemetry.sdk = NS(flush=lambda timeout_millis: calls.append(("flush", timeout_millis)) or False,
                           shutdown=lambda timeout_millis: calls.append(("shutdown", timeout_millis)))
        with self.assertLogs("agentforge.telemetry.neatlogs_setup", level=logging.WARNING) as logs:
            telemetry.shutdown()
        self.assertEqual(calls, [("flush", 10000), ("shutdown", 10000)])
        self.assertIn("last traces may be lost", logs.output[0])

    def test_a_failing_flush_does_not_stop_the_close(self):
        calls = []

        def broken(timeout_millis):
            raise OSError("network down")
        telemetry = untraced()
        telemetry.sdk = NS(flush=broken, shutdown=lambda timeout_millis: calls.append("shutdown"))
        with self.assertLogs("agentforge.telemetry.neatlogs_setup", level=logging.WARNING):
            telemetry.shutdown()
        self.assertEqual(calls, ["shutdown"])

    def test_a_span_the_sdk_cannot_open_runs_the_body_untraced(self):
        def broken(*args, **kwargs):
            raise RuntimeError("no tracer")
        telemetry = untraced()
        telemetry.sdk = NS(trace=broken)
        ran = []
        with self.assertLogs("agentforge.telemetry.neatlogs_setup", level=logging.WARNING):
            with telemetry.span("x") as span:
                ran.append(span)
        self.assertEqual(ran, [None])

    def test_an_exception_in_a_span_marks_it_failed_and_propagates_unchanged(self):
        events = []

        class FakeSpan:
            status = None

            def set_attribute(self, key, value):
                events.append(("attr", key, value))

            def set_status(self, status):
                events.append(("status", status.status_code.name))

            def record_exception(self, error):
                events.append(("exception", type(error).__name__))

        @contextmanager
        def trace(name, kind=None, **identity):
            events.append(("open", name, kind, identity))
            yield FakeSpan()
        telemetry = untraced()
        telemetry.sdk = NS(trace=trace)

        class Coded(Exception):
            code = "RATE_LIMIT"
        with self.assertRaises(Coded):
            with telemetry.span("m", kind="LLM", session_id="s", end_user_id="u"):
                raise Coded("provider said private text")
        self.assertEqual(events[0], ("open", "m", "LLM", {"session_id": "s", "end_user_id": "u"}))
        self.assertIn(("attr", "agentforge.error.type", "Coded"), events)
        self.assertIn(("attr", "agentforge.error.code", "RATE_LIMIT"), events)
        self.assertIn(("status", "ERROR"), events)
        self.assertIn(("exception", "Coded"), events)
        self.assertNotIn(("status", "OK"), events)

    def test_mark_error_survives_a_span_that_cannot_record(self):
        def closed(*args):
            raise RuntimeError("closed")
        mark_error(None, ValueError("x"))
        mark_error(NS(set_attribute=closed), ValueError("x"), code="X")

    def test_cancellation_is_recorded_as_such(self):
        attributes = {}
        span = NS(set_attribute=attributes.__setitem__, set_status=lambda status: None, record_exception=lambda error: None)
        mark_error(span, asyncio.CancelledError())
        self.assertIs(attributes["agentforge.cancelled"], True)
        self.assertEqual(attributes["agentforge.error.type"], "CancelledError")


class RunnerTracingTests(unittest.TestCase):
    """The worker's tracing helpers are best effort: whatever they cannot read, the task still runs."""

    def test_the_person_is_the_end_user_even_when_the_conversation_cannot_be_read(self):
        from agentforge.workers.runner import trace_identity

        def broken():
            raise OSError("database down")
        identity = trace_identity(broken, NS(id="t", created_by="user-1"))
        self.assertEqual(identity, {"end_user_id": "user-1"})

    def test_the_conversation_is_the_session(self):
        from agentforge.workers.runner import trace_identity

        class Db:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def scalar(self, statement):
                return "conversation-1"
        self.assertEqual(trace_identity(lambda: Db(), NS(id="t", created_by="user-1")),
                         {"end_user_id": "user-1", "session_id": "conversation-1"})

    def test_recording_the_outcome_never_raises(self):
        from agentforge.workers.runner import note_outcome

        def broken():
            raise OSError("database down")
        note_outcome(broken, NS(task=lambda: (_ for _ in ()).throw(RuntimeError("gone"))))
        note_outcome(broken, NS(task=lambda: NS(id="t", status="COMPLETED", error_code=None)))


if __name__ == "__main__":
    unittest.main()
