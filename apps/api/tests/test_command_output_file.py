"""A command whose output had to be cut leaves the whole of it in the sandbox, so the agent reads the part it needs instead of
running the command again (integrations/e2b_sandbox.py, tools/sandbox_shell.py)."""
from types import SimpleNamespace as NS
from unittest import IsolatedAsyncioTestCase

from agentforge.integrations.e2b_sandbox import OUTPUT_DIR
from agentforge.tools.contracts import BashArgs

from test_sandbox_shell import Env


class CommandOutputTests(IsolatedAsyncioTestCase):
    async def test_a_cut_output_is_saved_whole_in_the_sandbox_and_the_path_is_returned(self):
        long = "\n".join(f"line {index} " + "x" * 40 for index in range(2000))
        env = Env(lambda command: (0, long, "") if "make" in command else None)
        outcome = await env.call("bash", BashArgs(command="make test"))
        self.assertTrue(outcome.data["truncated"])
        path = outcome.data["output_file"]
        self.assertTrue(path.startswith(OUTPUT_DIR + "/") and path.endswith(".log"))
        self.assertEqual(env.sandbox.stored[path], long)
        self.assertIn("characters omitted", outcome.data["output"])

    async def test_a_short_output_saves_nothing_and_names_no_file(self):
        env = Env(lambda command: (0, "fine\n", "") if "true" in command else None)
        outcome = await env.call("bash", BashArgs(command="true"))
        self.assertNotIn("output_file", outcome.data)
        self.assertEqual([path for path in env.sandbox.written if path.endswith(".log")], [])

    async def test_a_sandbox_that_cannot_keep_the_file_still_returns_the_cut_output(self):
        env = Env(lambda command: (0, "y" * 50_000, "") if "big" in command else None)
        async def refuse(path, data, **kwargs):
            raise RuntimeError("disk full")
        env.sandbox.files = NS(read=env.sandbox._read, write=refuse)
        outcome = await env.call("bash", BashArgs(command="big"))
        self.assertTrue(outcome.data["truncated"])
        self.assertNotIn("output_file", outcome.data)
