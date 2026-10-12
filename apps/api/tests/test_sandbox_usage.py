"""Sandbox usage evidence: the signed E2B webhook parser and the cost estimates built from it. Offline, no database."""
import base64
from datetime import datetime, timezone
import hashlib
import json
from unittest import TestCase

from agentforge.api.routes_sandbox_usage import IgnoredEvent, verified_event
from agentforge.operations.business_metrics import sandbox_costs, validate_external


def signed(payload, secret):
    raw = json.dumps(payload).encode()
    return raw, base64.b64encode(hashlib.sha256(secret.encode() + raw).digest()).decode().rstrip('=')


class SandboxUsageTests(TestCase):
    def test_vendor_signature_and_replay_identity(self):
        secret = 's' * 32
        payload = {'id': 'event-1', 'sandbox_id': 'vm', 'type': 'sandbox.lifecycle.paused', 'timestamp': '2026-10-01T00:00:00Z',
                   'event_data': {'sandbox_metadata': {'agentforge_task': 'task', 'secret': 'never store'}}}
        raw, sig = signed(payload, secret)
        result = verified_event(raw, sig, secret)
        self.assertEqual(result['event_id'], 'event-1')
        self.assertNotIn('never store', json.dumps(result['payload_json']))
        with self.assertRaises(ValueError):
            verified_event(raw + b' ', sig, secret)

    def test_a_signed_event_of_an_unknown_kind_is_ignored_not_refused(self):
        # Refusing it would make E2B retry it and could get the endpoint disabled when the provider adds a new event type.
        secret = 's' * 32
        raw, sig = signed({'id': 'e', 'sandbox_id': 'vm', 'type': 'sandbox.lifecycle.something_new', 'timestamp': '2026-10-01T00:00:00Z'}, secret)
        with self.assertRaises(IgnoredEvent):
            verified_event(raw, sig, secret)

    def test_a_bad_signature_or_shape_is_still_refused(self):
        secret = 's' * 32
        raw, sig = signed({'id': 'e', 'sandbox_id': 'vm', 'type': 'sandbox.lifecycle.paused', 'timestamp': '2026-10-01T00:00:00Z'}, secret)
        with self.assertRaises(ValueError) as caught:
            verified_event(raw, sig, 'x' * 32)
        self.assertNotIsInstance(caught.exception, IgnoredEvent)
        raw, sig = signed({'id': 'e', 'type': 'sandbox.lifecycle.paused'}, secret)
        with self.assertRaises(ValueError) as caught:
            verified_event(raw, sig, secret)
        self.assertNotIsInstance(caught.exception, IgnoredEvent)

    def test_billed_runtime_not_tool_duration_and_no_double_count(self):
        since = datetime(2026, 10, 1, tzinfo=timezone.utc)
        data = {'execution_id': 'exec-1', 'execution': {'started_at': since.isoformat(), 'execution_time': 60000, 'vcpu_count': 2, 'memory_mb': 1024}}
        row = {'event_type': 'sandbox.lifecycle.paused', 'payload_json': data}
        rates = {'effective_from': since.isoformat(), 'vcpu_usd_per_second': '0.1', 'gib_usd_per_second': '0.01'}
        report = sandbox_costs([row, row], since, rates)
        self.assertEqual(report['running_seconds'], '60.0')
        self.assertEqual(float(report['rate_estimated_cost_usd']), 12.6)
        self.assertEqual(report['estimated_executions'], 1)
        self.assertFalse(report['coverage_complete'])

    def test_unknown_cost_does_not_claim_free_compute(self):
        since = datetime(2026, 10, 1, tzinfo=timezone.utc)
        row = {'event_type': 'sandbox.lifecycle.created', 'payload_json': {'execution_id': 'open'}}
        self.assertEqual(sandbox_costs([row], since)['incomplete_executions'], 1)

    def test_invalid_operator_money_refused(self):
        for value in ('NaN', 'Infinity', '-1'):
            with self.assertRaises(ValueError):
                validate_external({'monthly_costs': {'hosting': value}})
