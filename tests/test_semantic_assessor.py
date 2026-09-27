# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Offline contract tests: injected transport only, no provider requests."""
import copy
import io
import json
import math
import sys
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import semantic_assessor as sa

CONFIG = {"provider": "typesafe", "allow_cloud": True}
QUESTIONS = {
    "failure_kind": {"type": "choice", "instructions": "Classify the reported failure.",
                     "criteria": {"fixture": "Fixture is invalid", "agent": "Agent failed", "unknown": "Insufficient evidence"}},
    "rubric": {"type": "score", "instructions": "Rate coverage.", "criteria": ["Missing", "Partial", "Complete"]},
    "contradiction": {"type": "noul", "instructions": "Does the response contradict the evidence?"},
}
RESPONSE = {
    "model": sa.DEFAULT_MODEL,
    "answers": {
        "failure_kind": {"type": "choice", "choice": "agent", "probabilities": {"fixture": 0.1, "agent": 0.8, "unknown": 0.1}, "confidence": 0.7},
        "rubric": {"type": "score", "score": 1.9, "probabilities": {"0": 0.0, "1": 0.1, "2": 0.9}, "legend": {"0": "Missing", "1": "Partial", "2": "Complete"}, "confidence": 0.9},
        "contradiction": {"type": "noul", "noul": 0.95},
    },
    "usage": {"input_tokens": 99, "output_tokens": 0},
}


class AssessmentTests(unittest.TestCase):
    def assess(self, response=None, **kwargs):
        transport = Mock(return_value=json.dumps(RESPONSE if response is None else response).encode())
        config = kwargs.pop("config", CONFIG)
        result = sa.assess(config, {"diagnostic": "raw private details"}, QUESTIONS,
                           api_key="test-key-never-live", transport=transport, **kwargs)
        return result, transport

    def test_success_returns_only_validated_measurement(self):
        result, transport = self.assess()
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["measurement_available"])
        self.assertEqual(result["requests"], 1)
        self.assertEqual(len(result["question_hash"]), 64)
        self.assertGreaterEqual(result["latency_ms"], 0)
        self.assertNotIn("legend", result["answers"]["rubric"])
        self.assertNotIn("pass", result)
        serialized = json.dumps(result)
        self.assertNotIn("raw private", serialized)
        self.assertNotIn("test-key", serialized)
        self.assertNotIn("Classify", serialized)
        body, key, timeout, limit = transport.call_args.args
        self.assertEqual(json.loads(body)["model"], sa.DEFAULT_MODEL)
        self.assertEqual((key, timeout, limit), ("test-key-never-live", 10, 65536))
        transport.assert_called_once()

    def test_default_off_even_with_key(self):
        with patch.dict(sa.os.environ, {"TYPESAFE_API_KEY": "never-read"}):
            result, transport = self.assess(config={})
        self.assertEqual(result["status"], "disabled")
        self.assertFalse(result["measurement_available"])
        transport.assert_not_called()

    def test_off_and_unconsented_do_not_read_environment(self):
        for config in ({}, {"provider": "typesafe"}):
            with patch.object(sa.os.environ, "get", side_effect=AssertionError("must not read key")):
                result = sa.assess(config, "state", QUESTIONS)
            self.assertEqual(result["requests"], 0)

    def test_cloud_opt_in_and_key_required(self):
        transport = Mock()
        result = sa.assess({"provider": "typesafe"}, "state", QUESTIONS, api_key="test", transport=transport)
        self.assertEqual(result["error_category"], "cloud_not_allowed")
        with patch.dict(sa.os.environ, {}, clear=True):
            result = sa.assess(CONFIG, "state", QUESTIONS, transport=transport)
        self.assertEqual((result["status"], result["error_category"]), ("unavailable", "missing_key"))
        transport.assert_not_called()

    def test_invalid_config_and_request_never_send(self):
        cases = [
            (dict(CONFIG, provider="substitute"), "state", QUESTIONS),
            (dict(CONFIG, allow_cloud="true"), "state", QUESTIONS),
            (dict(CONFIG, required=1), "state", QUESTIONS),
            (dict(CONFIG, model="jev-latest"), "state", QUESTIONS),
            (dict(CONFIG, timeout_seconds=float("nan")), "state", QUESTIONS),
            (dict(CONFIG, timeout_seconds=61), "state", QUESTIONS),
            (dict(CONFIG, max_request_bytes=True), "state", QUESTIONS),
            (dict(CONFIG, endpoint="https://other.invalid"), "state", QUESTIONS),
            (CONFIG, "x" * 33000, QUESTIONS),
            (CONFIG, {"value": float("inf")}, QUESTIONS),
            (CONFIG, 42, QUESTIONS),
            (CONFIG, "state", {}),
            (CONFIG, "state", {"a": {"type": "prose", "instructions": "Explain"}}),
            (CONFIG, "state", {"a": {"type": "score", "instructions": "Rate", "criteria": ["Only"]}}),
            (CONFIG, "state", {"a": {"type": "noul", "instructions": "Judge", "criteria": {"maybe": "Maybe"}}}),
        ]
        for config, state, questions in cases:
            with self.subTest(config=config):
                transport = Mock()
                result = sa.assess(config, state, questions, api_key="fake", transport=transport)
                self.assertEqual(result["error_category"], "invalid_request")
                transport.assert_not_called()

    def test_provider_errors_safe_and_not_retried(self):
        for code, category in [(401, "authentication"), (403, "authentication"), (422, "provider_validation"), (429, "rate_limit"), (529, "overloaded"), (500, "http_error"), (302, "http_error")]:
            with self.subTest(code=code):
                transport = Mock(side_effect=urllib.error.HTTPError(sa.ENDPOINT, code, "SECRET BODY", {}, None))
                result = sa.assess(CONFIG, "state", QUESTIONS, api_key="SECRET KEY", transport=transport)
                self.assertEqual((result["status"], result["error_category"]), ("error", category))
                self.assertNotIn("SECRET", json.dumps(result))
                self.assertFalse(result["measurement_available"])
                transport.assert_called_once()

    def test_transport_errors_safe(self):
        for error, category in [(TimeoutError("secret"), "timeout"), (urllib.error.URLError(TimeoutError("secret")), "timeout"), (urllib.error.URLError("secret"), "network"), (RuntimeError("secret"), "transport_error")]:
            result = sa.assess(CONFIG, "state", QUESTIONS, api_key="key", transport=Mock(side_effect=error))
            self.assertEqual(result["error_category"], category)
            self.assertNotIn("secret", json.dumps(result))

    def test_invalid_credentials_never_sent(self):
        transport = Mock()
        result = sa.assess(CONFIG, "state", QUESTIONS, api_key="key\nHEADER", transport=transport)
        self.assertEqual(result["error_category"], "invalid_credential")
        transport.assert_not_called()

    def test_malformed_json_duplicate_keys_and_oversized_response(self):
        for raw in [b"not json SECRET", b'{"model":"a","model":"b"}', b'{"value":NaN}', b'\xff', b"x" * 65537, RESPONSE]:
            result = sa.assess(CONFIG, "state", QUESTIONS, api_key="key", transport=Mock(return_value=raw))
            self.assertEqual(result["error_category"], "invalid_response")
            self.assertNotIn("SECRET", json.dumps(result))

    def test_inconsistent_response_rejected(self):
        mutations = [
            lambda r: r.update(model="jev-9.0.0"),
            lambda r: r.update(extra="RAW"),
            lambda r: r.pop("usage"),
            lambda r: r["answers"].pop("rubric"),
            lambda r: r["answers"].update(extra={"type": "noul", "noul": 1}),
            lambda r: r["usage"].update(input_tokens=True),
            lambda r: r["usage"].update(input_tokens=-1),
            lambda r: r["answers"]["failure_kind"].update(choice="fixture"),
            lambda r: r["answers"]["failure_kind"].update(choice="invented"),
            lambda r: r["answers"]["failure_kind"]["probabilities"].update(agent=0.7),
            lambda r: r["answers"]["failure_kind"]["probabilities"].update(extra=0),
            lambda r: r["answers"]["failure_kind"].update(confidence=1.1),
            lambda r: r["answers"]["failure_kind"].update(confidence=True),
            lambda r: r["answers"]["rubric"].update(score=2),
            lambda r: r["answers"]["rubric"]["legend"].update({"2": "invented"}),
            lambda r: r["answers"]["contradiction"].update(noul=float("nan")),
            lambda r: r["answers"]["contradiction"].update(noul=-0.1),
            lambda r: r["answers"]["contradiction"].update(noul="0.9"),
            lambda r: r["answers"]["contradiction"].update(confidence=0.8),
        ]
        for mutate in mutations:
            response = copy.deepcopy(RESPONSE)
            mutate(response)
            result, _ = self.assess(response)
            self.assertEqual(result["error_category"], "invalid_response", response)
            self.assertEqual(result["answers"], {})
            self.assertFalse(result["measurement_available"])

    def test_low_confidence_and_escape_are_unknown(self):
        response = copy.deepcopy(RESPONSE)
        response["answers"]["failure_kind"]["confidence"] = 0.1
        result, _ = self.assess(response)
        self.assertEqual(result["status"], "unknown")
        self.assertFalse(result["measurement_available"])
        response = copy.deepcopy(RESPONSE)
        response["answers"]["failure_kind"].update(choice="unknown", probabilities={"fixture": 0.1, "agent": 0.1, "unknown": 0.8})
        result, _ = self.assess(response)
        self.assertEqual(result["status"], "unknown")
        response = copy.deepcopy(RESPONSE)
        response["answers"]["contradiction"]["noul"] = 0.5
        result, _ = self.assess(response)
        self.assertEqual(result["status"], "unknown")
        self.assertNotIn("confidence", result["answers"]["contradiction"])

    def test_required_missing_measurement_never_becomes_available(self):
        for config in [{"required": True}, {"provider": "typesafe", "required": True}]:
            result, transport = self.assess(config=config)
            self.assertTrue(result["required"])
            self.assertFalse(result["measurement_available"])
            self.assertNotEqual(result["status"], "ok")
            transport.assert_not_called()

    def test_invalid_request_preserves_required_measurement(self):
        result = sa.assess({'provider':'none','required':True,'typo':True}, 'state', QUESTIONS)
        self.assertTrue(result['required'])
        self.assertEqual(result['error_category'], 'invalid_request')
        self.assertFalse(result['measurement_available'])

    def test_cli_missing_file_preserves_required_measurement(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / 'config.json'
            config.write_text(json.dumps({'required': True}))
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(sa.main(['--config',str(config),'--state',str(config.parent/'missing'), '--questions',str(config.parent/'missing')]),1)
            result = json.loads(output.getvalue())
            self.assertTrue(result['required'])
            self.assertEqual(result['error_category'], 'input_file')

    def test_question_hash_order_independent_and_version_sensitive(self):
        first = sa.assess({}, "a", QUESTIONS)
        second = sa.assess({}, "b", dict(reversed(list(QUESTIONS.items()))))
        self.assertEqual(first["question_hash"], second["question_hash"])
        questions = copy.deepcopy(QUESTIONS)
        questions["contradiction"]["instructions"] += " Changed rubric"
        self.assertNotEqual(first["question_hash"], sa.assess({}, "a", questions)["question_hash"])

    def test_default_transport_refuses_redirects(self):
        self.assertIsNone(sa._NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.invalid"))

    def test_default_transport_fixed_endpoint_timeout_and_response_limit(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b"{}"
        opener = Mock()
        opener.open.return_value = response
        with patch.object(sa.urllib.request, "build_opener", return_value=opener):
            self.assertEqual(sa._http_transport(b"{}", "test", 3, 256), b"{}")
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, sa.ENDPOINT)
        self.assertEqual(request.method, "POST")
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 3)
        response.read.assert_called_once_with(257)

    def test_cli_refuses_to_append_to_campaign_ledger(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            paths = [directory / name for name in ('config.json','state.json','questions.json')]
            for path,value in zip(paths,[{},'state',QUESTIONS]):
                path.write_text(json.dumps(value))
            output = directory/'campaign.jsonl'
            original = '{"record_type":"campaign","campaign_id":"one"}\n'
            output.write_text(original)
            with redirect_stdout(io.StringIO()):
                self.assertEqual(sa.main(['--config',str(paths[0]),'--state',str(paths[1]),'--questions',str(paths[2]),'--append-attempt',str(output)]),1)
            self.assertEqual(output.read_text(),original)

    def test_cli_append_is_safe_and_append_only(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            paths = [directory / name for name in ("config.json", "state.json", "questions.json")]
            for path, value in zip(paths, [{"required": True}, {"diagnostic": "PRIVATE TEXT"}, QUESTIONS]):
                path.write_text(json.dumps(value))
            output = directory / "attempts.jsonl"
            args = ["--config", str(paths[0]), "--state", str(paths[1]), "--questions", str(paths[2]), "--append-attempt", str(output)]
            with redirect_stdout(io.StringIO()):
                self.assertEqual(sa.main(args), 1)
                self.assertEqual(sa.main(args), 1)
            records = [json.loads(line) for line in output.read_text().splitlines()]
            self.assertEqual(len(records), 2)
            self.assertTrue(all(r["status"] == "disabled" for r in records))
            self.assertNotIn("PRIVATE TEXT", output.read_text())
            self.assertNotIn("instructions", output.read_text())


if __name__ == "__main__":
    unittest.main()
