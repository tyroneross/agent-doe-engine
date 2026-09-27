# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Optional, bounded TypeSafe semantic measurement; never a promotion decision.

Contract checked 2026-09-27 against https://docs.typesafe.ai/api.md,
https://docs.typesafe.ai/models.md and https://docs.typesafe.ai/confidence.md.
Provider confidence describes a model distribution, not a statistical interval.
Only sanitized results are returned or appended; state, questions, credentials,
response bodies and exception messages are never persisted by this module.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-1.13.0"
CONFIG_KEYS = {"provider", "allow_cloud", "model", "required", "timeout_seconds",
               "max_request_bytes", "max_response_bytes", "min_confidence"}


class InvalidData(ValueError):
    """Validation failed. Deliberately carries no untrusted diagnostic text."""


def _require(condition):
    if not condition:
        raise InvalidData()


def _number(value, low, high):
    _require(type(value) in (int, float) and math.isfinite(value) and low <= value <= high)
    return value


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _json_tree(value, depth=0):
    _require(depth <= 24)
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float:
        _require(math.isfinite(value))
    elif type(value) is list:
        for item in value:
            _json_tree(item, depth + 1)
    elif type(value) is dict:
        for key, item in value.items():
            _require(type(key) is str)
            _json_tree(item, depth + 1)
    else:
        raise InvalidData()


def _description(value):
    _require(type(value) in (str, list, dict) and bool(value))
    _json_tree(value)


def _label(value):
    # Bounded identifiers prevent raw diagnostic prose from entering saved results.
    _require(type(value) is str and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", value))


def _questions(questions):
    _require(type(questions) is dict and 1 <= len(questions) <= 32)
    for name, question in questions.items():
        _label(name)
        _require(type(question) is dict and
                 {"type", "instructions"} <= question.keys() <= {"type", "instructions", "criteria"})
        _description(question["instructions"])
        kind, criteria = question["type"], question.get("criteria")
        if kind == "choice":
            _require(type(criteria) is dict and 2 <= len(criteria) <= 255)
            for label, description in criteria.items():
                _label(label)
                if description is not None:
                    _description(description)
        elif kind == "score":
            # Text levels allow exact validation of the provider's returned legend.
            _require(type(criteria) is list and 2 <= len(criteria) <= 10)
            _require(all(type(level) is str and bool(level.strip()) for level in criteria))
        elif kind == "noul":
            if "criteria" in question:
                _require(type(criteria) is dict and bool(criteria) and criteria.keys() <= {"true", "false"})
                for description in criteria.values():
                    _description(description)
        else:
            raise InvalidData()


def _load_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result)
            result[key] = value
        return result

    def invalid_constant(_):
        raise InvalidData()

    return json.loads(data, object_pairs_hook=pairs, parse_constant=invalid_constant)


def _response(response, questions, model):
    _require(type(response) is dict and response.keys() == {"model", "answers", "usage"})
    _require(response["model"] == model)
    answers, usage = response["answers"], response["usage"]
    _require(type(answers) is dict and answers.keys() == questions.keys())
    _require(type(usage) is dict and usage.keys() <= {"input_tokens", "output_tokens"})
    for count in usage.values():
        _require(count is None or (type(count) is int and count >= 0))
    safe = {}
    for name, question in questions.items():
        answer = answers[name]
        kind = question["type"]
        _require(type(answer) is dict and answer.get("type") == kind)
        if kind == "noul":
            _require(answer.keys() == {"type", "noul"})
            safe[name] = {"type": kind, "noul": _number(answer["noul"], 0, 1)}
            continue
        expected = {"type", "probabilities", "confidence"}
        expected |= {"choice"} if kind == "choice" else {"score", "legend"}
        _require(answer.keys() == expected)
        probabilities = answer["probabilities"]
        labels = (set(question["criteria"]) if kind == "choice" else
                  {str(i) for i in range(len(question["criteria"]))})
        _require(type(probabilities) is dict and probabilities.keys() == labels)
        for probability in probabilities.values():
            _number(probability, 0, 1)
        _require(math.isclose(sum(probabilities.values()), 1, rel_tol=0, abs_tol=1e-6))
        confidence = _number(answer["confidence"], 0, 1)
        clean = {"type": kind, "probabilities": dict(probabilities), "confidence": confidence}
        if kind == "choice":
            choice = answer["choice"]
            _require(type(choice) is str and choice in labels)
            _require(probabilities[choice] >= max(probabilities.values()) - 1e-9)
            clean["choice"] = choice
        else:
            score = _number(answer["score"], 0, len(labels) - 1)
            mean = sum(int(level) * p for level, p in probabilities.items())
            _require(math.isclose(score, mean, rel_tol=0, abs_tol=1e-6))
            _require(answer["legend"] == {str(i): level for i, level in enumerate(question["criteria"])})
            clean["score"] = score
            # Do not persist the rubric prose echoed by the provider.
        safe[name] = clean
    return safe, dict(usage)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _http_transport(body, api_key, timeout_seconds, max_response_bytes):
    """Send once to the fixed endpoint, refusing redirects and oversized bodies."""
    request = urllib.request.Request(ENDPOINT, data=body, method="POST", headers={
        "Content-Type": "application/json", "Authorization": "Bearer " + api_key})
    opener = urllib.request.build_opener(_NoRedirect())
    with opener.open(request, timeout=timeout_seconds) as response:
        raw = response.read(max_response_bytes + 1)
        _require(len(raw) <= max_response_bytes)
        return raw


def assess(config, state, questions, *, api_key=None, transport=None):
    """Return one semantic measurement or explicit missing/error/unknown status.

    `transport(body_bytes, key, timeout_seconds, max_response_bytes)` is injectable
    for offline tests and must return JSON bytes. Each invocation sends at most one
    request. `required` marks a required measurement; consumers must require status
    `ok` AND measurement_available before using it. This never decides pass/fail.
    No credential is read until both provider and cloud consent are explicit.
    """
    result = {"schema": "semantic-assessment.v1", "status": "error", "provider": "none",
              "model": DEFAULT_MODEL, "question_hash": None, "latency_ms": 0.0,
              "error_category": None,
              # Preserve a required measurement even when other config fields fail.
              "required": config.get("required", False) is not False if isinstance(config, dict) else True,
              "measurement_available": False,
              "requests": 0, "answers": {}, "usage": {},
              "confidence_kind": "model_distribution_not_statistical_interval"}

    def finish(status, category=None):
        result.update(status=status, error_category=category)
        return result

    try:
        _require(type(config) is dict and config.keys() <= CONFIG_KEYS)
        provider = config.get("provider", "none")
        _require(provider in ("none", "typesafe"))
        _require(type(config.get("allow_cloud", False)) is bool)
        _require(type(config.get("required", False)) is bool)
        model = config.get("model", DEFAULT_MODEL)
        _require(type(model) is str and re.fullmatch(r"jev-\d+\.\d+\.\d+", model))
        result.update(provider=provider, model=model, required=config.get("required", False))
        timeout = _number(config.get("timeout_seconds", 10), 0.1, 60)
        request_limit = config.get("max_request_bytes", 32768)
        response_limit = config.get("max_response_bytes", 65536)
        _require(type(request_limit) is int and 256 <= request_limit <= 65536)
        _require(type(response_limit) is int and 256 <= response_limit <= 262144)
        floor = _number(config.get("min_confidence", 0.5), 0, 1)
        _questions(questions)
        result["question_hash"] = hashlib.sha256(_json_bytes(questions)).hexdigest()
        _require(type(state) in (str, dict, list) and bool(state))
        _json_tree(state)
        body = _json_bytes({"state": state, "model": model, "questions": questions})
        _require(len(body) <= request_limit)
    except (InvalidData, ValueError, TypeError, OverflowError, RecursionError):
        return finish("error", "invalid_request")
    if provider == "none":
        return finish("disabled", "provider_disabled")
    if config.get("allow_cloud", False) is not True:
        return finish("unavailable", "cloud_not_allowed")
    key = api_key if api_key is not None else os.environ.get("TYPESAFE_API_KEY")
    if not isinstance(key, str) or not key.strip():
        return finish("unavailable", "missing_key")
    if any(ord(char) < 32 or ord(char) > 126 for char in key):
        return finish("error", "invalid_credential")
    start = time.monotonic()
    result["requests"] = 1
    try:
        raw = (transport or _http_transport)(body, key, timeout, response_limit)
        _require(type(raw) is bytes and len(raw) <= response_limit)
        answers, usage = _response(_load_json(raw), questions, model)
        result.update(answers=answers, usage=usage)
        uncertain = any(
            (answer["confidence"] < floor if answer["type"] != "noul"
             else abs(2 * answer["noul"] - 1) < floor)
            or (answer.get("choice") in {"unknown", "other", "none", "insufficient_evidence"})
            for answer in answers.values())
        result["measurement_available"] = not uncertain
        return finish("unknown" if uncertain else "ok", "uncertain" if uncertain else None)
    except urllib.error.HTTPError as error:
        category = {401: "authentication", 403: "authentication", 422: "provider_validation",
                    429: "rate_limit", 529: "overloaded"}.get(error.code, "http_error")
        return finish("error", category)
    except (TimeoutError, socket.timeout):
        return finish("error", "timeout")
    except urllib.error.URLError as error:
        return finish("error", "timeout" if isinstance(error.reason, (TimeoutError, socket.timeout)) else "network")
    except (InvalidData, ValueError, TypeError, UnicodeError, OverflowError, RecursionError):
        return finish("error", "invalid_response")
    except Exception:
        # Transport exception text may contain credentials or raw state.
        return finish("error", "transport_error")
    finally:
        result["latency_ms"] = round((time.monotonic() - start) * 1000, 3)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--questions", required=True, type=Path)
    parser.add_argument("--append-attempt", type=Path,
                        help="Append only sanitized semantic-assessment.v1 JSON to a dedicated JSONL file")
    args = parser.parse_args(argv)
    inputs = []
    try:
        for path in (args.config, args.state, args.questions):
            with path.open("rb") as stream:
                raw = stream.read(65537)
            _require(len(raw) <= 65536)
            inputs.append(_load_json(raw))
        result = assess(*inputs)
    except (OSError, ValueError, InvalidData, UnicodeError, RecursionError):
        result = assess(inputs[0] if inputs else {"required": True}, "", {})
        result["error_category"] = "input_file"
    if args.append_attempt:
        try:
            import fcntl  # The optional durable receipt stream uses Unix locks.
            with args.append_attempt.open("a+", encoding="utf-8") as stream:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
                stream.seek(0)
                last_line = ""
                for line in stream:
                    last_line = line
                    existing = _load_json(line)
                    _require(isinstance(existing, dict) and existing.get("schema") == "semantic-assessment.v1")
                stream.seek(0, 2)
                if last_line and not last_line.endswith("\n"):
                    stream.write("\n")
                stream.write(_json_bytes(result).decode("utf-8") + "\n")
                stream.flush()
        except (OSError, ValueError, InvalidData, UnicodeError, ImportError):
            result.update(status="error", error_category="persistence", measurement_available=False)
    print(_json_bytes(result).decode("utf-8"))
    return 0 if result["status"] in ("ok", "disabled") and not (result["required"] and not result["measurement_available"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
