# SPDX-License-Identifier: Apache-2.0
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import experiment_ledger as ledger


@pytest.fixture
def path(tmp_path):
    path = tmp_path / "campaign.jsonl"
    ledger.init_campaign(path, {"campaign_id": "fixture", "title": "Deterministic fixture", "plan": {"objective": "accuracy"}})
    return path


def attempt(**changes):
    return {"attempt_id": "a1", "batch_id": "b1", "cell_id": "c1", "phase": "screening",
            "settings": {"model": "local"}, "metrics": {"accuracy": 0.5, "cost": None},
            "measurements": {"accuracy": {"command": "printf 0.5", "method": "exact", "unit": "fraction", "n": 2}},
            "guard_ok": False, "error": "fixture failed", **changes}


def test_append_preserves_failures_and_chain(path):
    first_bytes = path.read_bytes()
    first = ledger.append_attempt(path, attempt())
    second = ledger.append_attempt(path, attempt(attempt_id="a2", metrics={"accuracy": 1.0}, guard_ok=True, error=None))
    decision = ledger.append_decision(path, {"decision_id": "d1", "attempt_ids": ["a1", "a2"], "reason": "Repeat before accepting", "next_change": "Increase sample size"})
    records = ledger.load_ledger(path)
    assert path.read_bytes().startswith(first_bytes)
    assert first["metrics"]["cost"] is None and first["guard_ok"] is False
    assert second["prev_hash"] == first["record_hash"]
    assert decision["prev_hash"] == second["record_hash"]
    assert all(r["plan_hash"] == records[0]["plan_hash"] for r in records)


@pytest.mark.parametrize("changes", [
    {"campaign_id": "wrong"}, {"phase": ""}, {"attempt_id": None}, {"batch_id": ""},
    {"metrics": {"x": float("nan")}}, {"metrics": {"x": float("inf")}},
    {"metrics": {"x": True}}, {"metrics": {"x": "0.5"}}, {"metrics": []},
    {"settings": []}, {"guard_ok": "false"}, {"guard_ok": 1}, {"error": {}},
    {"recorded_at": "2026-09-27T00:00:00Z"}, {"executed_at": "yesterday"},
    {"measurements": {"x": {"n": -1}}}, {"measurements": {"x": {"n": True}}},
    {"measurements": {"x": {"unit": 1}}}, {"plan_hash": "wrong"},
])
def test_invalid_attempt_never_changes_existing_bytes(path, changes):
    before = path.read_bytes()
    with pytest.raises((ValueError, TypeError)):
        ledger.append_attempt(path, attempt(**changes))
    assert path.read_bytes() == before


def test_duplicate_attempt_and_changed_cell_are_rejected(path):
    ledger.append_attempt(path, attempt())
    before = path.read_bytes()
    with pytest.raises(ValueError, match="Duplicate"):
        ledger.append_attempt(path, attempt())
    with pytest.raises(ValueError, match="Settings changed"):
        ledger.append_attempt(path, attempt(attempt_id="a2", settings={"model": "other"}))
    assert path.read_bytes() == before
    ledger.append_attempt(path, attempt(attempt_id="a2", cell_id="c2", settings={"model": "other"}))


@pytest.mark.parametrize("first,second", [(1, True), (0, False), ([1], [True]), ({"x": 1}, {"x": True})])
def test_same_cell_rejects_boolean_numeric_setting_changes(path, first, second):
    ledger.append_attempt(path, attempt(settings={"factor": first}))
    before = path.read_bytes()
    with pytest.raises(ValueError, match="Settings changed"):
        ledger.append_attempt(path, attempt(attempt_id="a2", settings={"factor": second}))
    assert path.read_bytes() == before
    assert len(ledger.load_ledger(path)) == 2


def test_decision_references_and_proposed_amendment(path):
    ledger.append_attempt(path, attempt())
    decision = {"decision_id": "d1", "attempt_ids": ["a1"], "reason": "Need confirmation", "next_change": "New fixture"}
    first = ledger.append_decision(path, decision)
    proposed = {"objective": "accuracy", "fixture": "v2"}
    second = ledger.append_decision(path, {**decision, "decision_id": "d2", "parent_decision_id": "d1", "parent_batch_id": "b1", "new_plan": proposed, "new_plan_hash": ledger.content_hash(proposed)})
    assert second["parent_decision_hash"] == first["record_hash"]
    assert second["plan_hash"] != second["new_plan_hash"]
    for changes in ({"decision_id": "d1"}, {"attempt_ids": ["missing"]}, {"attempt_ids": ["a1", "a1"]}, {"parent_decision_id": "missing"}, {"parent_batch_id": "missing"}, {"new_plan_hash": "no"}):
        before = path.read_bytes()
        with pytest.raises(ValueError):
            ledger.append_decision(path, {**decision, "decision_id": "d3", **changes})
        assert path.read_bytes() == before


def test_tampered_or_truncated_ledger_is_rejected(path):
    ledger.append_attempt(path, attempt())
    text = path.read_text()
    path.write_text(text.replace('"accuracy": 0.5', '"accuracy": 0.9'))
    with pytest.raises(ValueError, match="hash"):
        ledger.load_ledger(path)
    path.write_text(text + '{"unfinished":')
    before = path.read_bytes()
    with pytest.raises(ValueError, match="line 3"):
        ledger.append_attempt(path, attempt(attempt_id="a2"))
    assert path.read_bytes() == before


def test_campaign_initialization_cannot_overwrite(path):
    before = path.read_bytes()
    with pytest.raises(ValueError, match="already initialized"):
        ledger.init_campaign(path, {"campaign_id": "other", "title": "other", "plan": {}})
    assert path.read_bytes() == before


def test_cli_round_trip(tmp_path, capsys):
    path = tmp_path / "ledger.jsonl"
    record = tmp_path / "input.json"
    record.write_text(json.dumps({"campaign_id": "cli", "title": "CLI fixture", "plan": {}}))
    ledger.main(["init", "--ledger", str(path), "--record", str(record)])
    record.write_text(json.dumps(attempt()))
    ledger.main(["append-attempt", "--ledger", str(path), "--record", str(record)])
    ledger.main(["validate", "--ledger", str(path)])
    assert json.loads(capsys.readouterr().out.splitlines()[-1]) == {"valid": True, "records": 2}


def test_no_campaign_and_invalid_lines(tmp_path):
    path = tmp_path / "ledger.jsonl"
    with pytest.raises(ValueError, match="Initialize"):
        ledger.append_attempt(path, attempt())
    for content in ("null\n", "[]\n", "{}\n", "\n", "not json\n"):
        path.write_text(content)
        with pytest.raises(ValueError, match="line 1"):
            ledger.load_ledger(path)


def test_parallel_appends_keep_one_valid_chain(path):
    def append(index):
        return ledger.append_attempt(path, attempt(attempt_id=f"a{index}"))
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(append, range(12)))
    records = ledger.load_ledger(path)
    assert len(records) == 13
    assert len({r["attempt_id"] for r in records[1:]}) == 12


@pytest.mark.parametrize("receipt", ["error", [], {}, {"required": "true", "status": "error"},
    {"required": 1, "status": "ok"}, {"required": False, "status": []},
    {"required": True, "status": ""}, {"required": True, "status": "ok", "measurement_available": 1}])
def test_malformed_semantic_receipt_cannot_be_appended(path, receipt):
    before = path.read_bytes()
    with pytest.raises(ValueError, match="semantic_assessment"):
        ledger.append_attempt(path, attempt(semantic_assessment=receipt))
    assert path.read_bytes() == before


@pytest.mark.parametrize("tail", ['{"unfinished":', '{"complete_json":true}'])
def test_incomplete_final_line_explains_recovery_without_changing_bytes(path, tail):
    path.write_text(path.read_text() + tail)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="line 2 is incomplete; preserve the ledger and recover"):
        ledger.append_attempt(path, attempt())
    assert path.read_bytes() == before


@pytest.mark.parametrize("receipt", [None, {"required": False, "status": "disabled", "measurement_available": False},
    {"required": True, "status": "error", "measurement_available": False},
    {"required": True, "status": "ok", "measurement_available": True}])
def test_valid_semantic_receipts_remain_recordable(path, receipt):
    record = ledger.append_attempt(path, attempt(semantic_assessment=receipt))
    assert ledger.load_ledger(path)[-1]["semantic_assessment"] == record["semantic_assessment"]
