#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Append-only experiment records. Commands record evidence; they never run it.

One JSONL file contains a campaign followed by attempts and decisions. Every
record hashes its content and links to the previous record. This detects edits,
not malicious rewriting of the entire file; keep an external hash for that.
Append operations take an exclusive advisory lock on Unix hosts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import fcntl

SCHEMA_VERSION = 1
SYSTEM_FIELDS = {"schema_version", "record_type", "recorded_at", "prev_hash", "record_hash"}


def _finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Nonfinite numbers are not allowed; record missing metrics as null")
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("JSON object keys must be strings")
        for item in value.values():
            _finite(item)
    elif isinstance(value, list):
        for item in value:
            _finite(item)


def content_hash(value):
    _finite(value)
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _text(record, key):
    if not isinstance(record.get(key), str) or not record[key].strip():
        raise ValueError(f"{key} must be a nonempty string")


def _timestamp(value, field):
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO 8601 timestamp with timezone")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.utcoffset() is None:
            raise ValueError()
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO 8601 timestamp with timezone") from exc


def validate_semantic_assessment(receipt):
    """Check optional receipt structure without turning a missing result into success."""
    if receipt is None:
        return
    if not isinstance(receipt, dict) or type(receipt.get("required")) is not bool:
        raise ValueError("semantic_assessment requires a boolean required field")
    if not isinstance(receipt.get("status"), str) or not receipt["status"].strip():
        raise ValueError("semantic_assessment requires a nonempty status string")
    if "measurement_available" in receipt and type(receipt["measurement_available"]) is not bool:
        raise ValueError("semantic_assessment measurement_available must be a boolean")


def _validate(record, previous):
    if not isinstance(record, dict):
        raise ValueError("Each record must be a JSON object")
    _finite(record)
    if type(record.get("schema_version")) is not int or record["schema_version"] != SCHEMA_VERSION:
        raise ValueError("Unsupported ledger schema_version")
    _text(record, "campaign_id")
    _timestamp(record.get("recorded_at"), "recorded_at")
    expected_parent = previous[-1]["record_hash"] if previous else None
    if record.get("prev_hash") != expected_parent:
        raise ValueError("Broken previous-record hash link")
    expected_hash = content_hash({k: v for k, v in record.items() if k != "record_hash"})
    if record.get("record_hash") != expected_hash:
        raise ValueError("Record hash does not match content")
    kind = record.get("record_type")
    if not previous:
        if kind != "campaign":
            raise ValueError("The first record must initialize a campaign")
        _text(record, "title")
        if not isinstance(record.get("plan"), dict):
            raise ValueError("Campaign plan must be an object")
        if record.get("plan_hash") != content_hash(record["plan"]):
            raise ValueError("Campaign plan_hash does not match plan")
        return
    if record["campaign_id"] != previous[0]["campaign_id"]:
        raise ValueError("campaign_id does not match the initialized campaign")
    if record.get("plan_hash") != previous[0]["plan_hash"]:
        raise ValueError("plan_hash does not match the initialized plan")
    attempts = {r["attempt_id"]: r for r in previous if r["record_type"] == "attempt"}
    decisions = {r["decision_id"]: r for r in previous if r["record_type"] == "decision"}
    if kind == "attempt":
        for key in ("attempt_id", "batch_id", "cell_id", "phase"):
            _text(record, key)
        if record["attempt_id"] in attempts:
            raise ValueError("Duplicate attempt_id; retries require a new attempt_id")
        if not isinstance(record.get("settings"), dict):
            raise ValueError("Attempt settings must be an object")
        for earlier in attempts.values():
            if (earlier["batch_id"], earlier["cell_id"]) == (record["batch_id"], record["cell_id"]) and content_hash(earlier["settings"]) != content_hash(record["settings"]):
                raise ValueError("Settings changed for an existing batch/cell; assign a new cell_id")
        metrics = record.get("metrics")
        if not isinstance(metrics, dict):
            raise ValueError("Attempt metrics must be an object")
        for name, value in metrics.items():
            if not name.strip() or (value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)))):
                raise ValueError("Metric values must be numbers or null")
        if record.get("guard_ok") is not None and type(record["guard_ok"]) is not bool:
            raise ValueError("guard_ok must be true, false or null")
        validate_semantic_assessment(record.get("semantic_assessment"))
        if record.get("error") is not None and not isinstance(record["error"], str):
            raise ValueError("error must be a string or null")
        if record.get("change") is not None and not isinstance(record["change"], str):
            raise ValueError("change must be a string or null")
        if record.get("executed_at") is not None:
            _timestamp(record["executed_at"], "executed_at")
        measurements = record.get("measurements", {})
        if not isinstance(measurements, dict):
            raise ValueError("measurements must be an object keyed by metric")
        for name, measurement in measurements.items():
            if not isinstance(measurement, dict):
                raise ValueError(f"Measurement {name} must be an object")
            for field in ("command", "method", "unit"):
                if measurement.get(field) is not None and not isinstance(measurement[field], str):
                    raise ValueError(f"Measurement {field} must be a string or null")
            n = measurement.get("n")
            if n is not None and (type(n) is not int or n < 0):
                raise ValueError("Measurement n must be a nonnegative integer or null")
    elif kind == "decision":
        for key in ("decision_id", "reason", "next_change"):
            _text(record, key)
        if record["decision_id"] in decisions:
            raise ValueError("Duplicate decision_id")
        refs = record.get("attempt_ids")
        if not isinstance(refs, list) or any(not isinstance(ref, str) or ref not in attempts for ref in refs):
            raise ValueError("attempt_ids must reference recorded attempts")
        if len(refs) != len(set(refs)):
            raise ValueError("Duplicate attempt reference")
        parent = record.get("parent_decision_id")
        if parent is not None and parent not in decisions:
            raise ValueError("parent_decision_id must reference an earlier decision")
        if record.get("parent_decision_hash") != (decisions[parent]["record_hash"] if parent else None):
            raise ValueError("Decision parent hash does not match its parent")
        if record.get("new_plan_hash") is not None:
            if not isinstance(record["new_plan_hash"], str) or not re.fullmatch(r"[0-9a-f]{64}", record["new_plan_hash"]):
                raise ValueError("new_plan_hash must be a SHA-256 digest")
            if "new_plan" in record and (not isinstance(record["new_plan"], dict) or content_hash(record["new_plan"]) != record["new_plan_hash"]):
                raise ValueError("new_plan_hash does not match the proposed new_plan")
        elif "new_plan" in record:
            raise ValueError("A proposed new_plan requires its new_plan_hash")
        if record.get("parent_batch_id") is not None and record["parent_batch_id"] not in {a["batch_id"] for a in attempts.values()}:
            raise ValueError("parent_batch_id must reference an earlier recorded batch")
    else:
        raise ValueError("Only attempt or decision records can follow a campaign")


def _read_lines(lines):
    records = []
    for number, line in enumerate(lines, 1):
        try:
            record = json.loads(line)
            _validate(record, records)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Ledger line {number}: {exc}") from exc
        records.append(record)
    return records


def load_ledger(path):
    with Path(path).open(encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_SH)
        records = _read_lines(handle)
    if not records:
        raise ValueError("Ledger is empty; initialize a campaign first")
    return records


def _append(path, payload, kind):
    if not isinstance(payload, dict):
        raise ValueError("Record input must be a JSON object")
    if SYSTEM_FIELDS.intersection(payload):
        raise ValueError("Record input cannot supply system-managed hash, type or timestamp fields")
    # Serialize before opening the file; invalid input cannot create a partial ledger.
    record = json.loads(json.dumps(payload, allow_nan=False))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        handle.seek(0)
        existing_text = handle.read()
        if existing_text and not existing_text.endswith("\n"):
            line_number = existing_text.count("\n") + 1
            raise ValueError(f"Ledger line {line_number} is incomplete; preserve the ledger and recover the final record before appending")
        previous = _read_lines(existing_text.splitlines())
        if kind == "campaign" and previous:
            raise ValueError("Campaign is already initialized; existing records were preserved")
        if kind != "campaign" and not previous:
            raise ValueError("Initialize a campaign before appending records")
        record.update(schema_version=SCHEMA_VERSION, record_type=kind,
                      recorded_at=datetime.now(timezone.utc).isoformat(),
                      prev_hash=previous[-1]["record_hash"] if previous else None)
        if kind == "campaign":
            record["plan_hash"] = content_hash(record.get("plan"))
        else:
            record.setdefault("campaign_id", previous[0]["campaign_id"])
            record.setdefault("plan_hash", previous[0]["plan_hash"])
        if kind == "decision":
            parent = record.get("parent_decision_id")
            parents = [r for r in previous if r.get("decision_id") == parent and r["record_type"] == "decision"]
            record.setdefault("parent_decision_hash", parents[-1]["record_hash"] if parents else None)
        record["record_hash"] = content_hash(record)
        _validate(record, previous)
        handle.seek(0, os.SEEK_END)
        handle.write(json.dumps(record, sort_keys=True, allow_nan=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return record


def init_campaign(path, campaign):
    return _append(path, campaign, "campaign")


def append_attempt(path, attempt):
    return _append(path, attempt, "attempt")


def append_decision(path, decision):
    return _append(path, decision, "decision")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("init", "append-attempt", "append-decision", "validate"))
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--record", type=Path, help="JSON object file; never executed")
    args = parser.parse_args(argv)
    try:
        if args.action == "validate":
            print(json.dumps({"valid": True, "records": len(load_ledger(args.ledger))}))
        else:
            if not args.record:
                parser.error("--record is required for writes")
            payload = json.loads(args.record.read_text(encoding="utf-8"))
            function = {"init": init_campaign, "append-attempt": append_attempt, "append-decision": append_decision}[args.action]
            print(json.dumps(function(args.ledger, payload), allow_nan=False))
    except (OSError, ValueError, TypeError) as exc:
        parser.exit(2, f"Ledger error: {exc}\n")


if __name__ == "__main__":
    main()
