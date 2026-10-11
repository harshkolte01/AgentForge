"""The delivery HTTP surface: paths and bodies as docs/delivery.md promises, and failures as machine-readable codes."""
from types import SimpleNamespace as NS
from unittest import TestCase
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentforge.api.routes_delivery import router
from agentforge.auth.session import current_user
from agentforge.delivery.service import DeliveryError
from agentforge.integrations.github_client import GitHubError

SESSION = uuid4()
KEY = str(uuid4())
HEAD = "a" * 40


class Recorder:
    """Stands in for the DeliveryService: answers every call with a marker and remembers what it was asked."""

    def __init__(self):
        self.calls, self.error = [], None

    def __getattr__(self, name):
        async def call(*args, **kwargs):
            self.calls.append((name, args[2:] if name == "discard_file" else (), kwargs))
            if self.error:
                raise self.error
            return {"called": name}
        return call


class RouteTests(TestCase):
    def setUp(self):
        self.user = NS(id=uuid4())
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[current_user] = lambda: self.user
        self.service = app.state.delivery_service = Recorder()
        self.client = TestClient(app)
        self.base = f"/api/v1/sessions/{SESSION}/delivery"

    def test_every_documented_endpoint_exists(self):
        paths = {(method, route.path.removeprefix(router.prefix)) for route in router.routes for method in route.methods}
        self.assertEqual(paths, {("GET", ""), ("GET", "/changes"), ("POST", "/suggest"), ("POST", "/push"), ("POST", "/pull-request"),
            ("POST", "/ready"), ("POST", "/merge"), ("POST", "/close"), ("POST", "/update-branch"), ("POST", "/discard-file"),
            ("POST", "/refresh"), ("POST", "/reset")})

    def test_reads_and_actions_reach_the_service_with_the_callers_request(self):
        self.assertEqual(self.client.get(self.base).json(), {"called": "view"})
        self.assertEqual(self.client.get(self.base + "/changes").json(), {"called": "changes"})
        self.assertEqual(self.client.post(self.base + "/push", json={"idempotency_key": KEY, "message": "Fix"}).json(), {"called": "push"})
        name, _, kwargs = self.service.calls[-1]
        self.assertEqual((kwargs["key"], kwargs["message"], kwargs["branch"]), (KEY, "Fix", None))
        self.client.post(self.base + "/pull-request", json={"idempotency_key": KEY, "title": "T", "body": "B", "draft": False})
        _, _, kwargs = self.service.calls[-1]
        self.assertEqual((kwargs["title"], kwargs["body"], kwargs["draft"], kwargs["message"]), ("T", "B", False, None))
        self.client.post(self.base + "/merge", json={"idempotency_key": KEY, "method": "squash", "expected_head_sha": HEAD})
        _, _, kwargs = self.service.calls[-1]
        self.assertEqual((kwargs["method"], kwargs["expected_head_sha"]), ("squash", HEAD))
        self.client.post(self.base + "/reset", json={"idempotency_key": KEY})
        self.assertIs(self.service.calls[-1][2]["discard_unpublished"], False)
        self.client.post(self.base + "/discard-file", json={"path": "src/a.py"})
        self.assertEqual(self.service.calls[-1][1], ("src/a.py",))

    def test_requests_that_do_not_match_the_contract_are_refused_before_the_service_is_asked(self):
        bad = [
            ("/push", {"message": "no key"}), ("/push", {"idempotency_key": "not-a-uuid", "message": "x"}),
            ("/push", {"idempotency_key": KEY, "message": ""}), ("/push", {"idempotency_key": KEY, "message": "x", "force": True}),
            ("/merge", {"idempotency_key": KEY, "method": "octopus", "expected_head_sha": HEAD}),
            ("/merge", {"idempotency_key": KEY, "method": "merge", "expected_head_sha": "abc"}),
            ("/pull-request", {"idempotency_key": KEY, "title": ""}), ("/discard-file", {"path": ""}),
        ]
        for path, body in bad:
            with self.subTest(path=path, body=body):
                self.assertEqual(self.client.post(self.base + path, json=body).status_code, 422)
        self.assertEqual(self.service.calls, [])

    def test_failures_are_machine_readable_codes_with_the_right_status(self):
        cases = [
            (DeliveryError("agent_running"), 409, "agent_running"), (DeliveryError("session_not_found"), 404, "session_not_found"),
            (DeliveryError("github_write_permission_required"), 403, "github_write_permission_required"),
            (DeliveryError("github_path_conflict:a.py,b.py"), 409, "github_path_conflict:a.py,b.py"),
            (DeliveryError("delivery_outcome_unknown"), 502, "delivery_outcome_unknown"),
            (DeliveryError("github_rate_limited"), 503, "github_rate_limited"),
            (DeliveryError("github_reauthorization_required"), 401, "github_reauthorization_required"),
            (GitHubError("github_write_conflict"), 409, "github_branch_moved"), (GitHubError("github_unavailable"), 503, "github_unavailable"),
        ]
        for error, status, detail in cases:
            self.service.error = error
            response = self.client.get(self.base)
            with self.subTest(detail=detail):
                self.assertEqual((response.status_code, response.json()["detail"]), (status, detail))

    def test_nothing_else_leaks_into_a_failure(self):
        self.service.error = DeliveryError("agent_running")
        self.assertEqual(set(self.client.get(self.base).json()), {"detail"})
