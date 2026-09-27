# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Promotion must bind valid confirmation evidence to the selected candidate."""
import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import doe


@pytest.fixture
def campaign(tmp_path):
    matrix = doe.full_factorial_2level(2)
    factors = [{"name": "a", "low": 0, "high": 1},
               {"name": "b", "levels": ["off", "on"]}]
    runs = doe.map_levels(matrix, factors)
    rows = []
    for i, (a, b) in enumerate(matrix):
        identity = {"candidate_id": f"candidate-{i}", "config_hash": f"config-{i}"}
        runs[i].update(identity)
        for offset in (-1, 0, 1):
            rows.append({"run_id": i, "values": {"latency": 100 - 20 * a + 2 * b + offset},
                         "guard_ok": True, **identity, "fixture_id": "fixture-v1",
                         "scorer_id": "scorer-v1", "split_id": "screening",
                         "item_ids": [f"screen-{i}"]})
    objs = [{"name": "latency", "direction": "lower", "role": "primary",
             "driver": "response time", "target": 85}]
    candidate = {"run_id": 2, "candidate_id": "candidate-2", "config_hash": "config-2"}
    conf = [{**candidate, "attempt_id": f"confirm-{i}", "values": {"latency": 78 + d}, "guard_ok": True,
             "fixture_id": "fixture-v1", "scorer_id": "scorer-v1", "split_id": "holdout",
             "item_ids": [f"holdout-{i}"]} for i, d in enumerate((-1, 0, 1, .5, -.5))]
    contract = {"schema_version": 1, "candidate": candidate, "fixture_id": "fixture-v1",
                "scorer_id": "scorer-v1", "objectives": copy.deepcopy(objs),
                "selection": "scalarize",
                "split": {"screening_id": "screening", "confirmation_id": "holdout", "independent": True},
                "measurement": {"valid": True, "evidence": "measurement-review.md"},
                "review": {"approved": True, "reviewer": "reviewer-1", "evidence": "review.md"}}
    return {"root": tmp_path, "design": {"matrix": matrix.tolist(), "factors": factors,
            "runs": runs, "design": {"type": "full"}}, "rows": rows, "conf": conf,
            "objs": objs, "contract": contract}


def run(campaign, capsys, command="confirm", contract=True, extra=()):
    root = campaign["root"]
    for key in ("design", "objs", "contract"):
        (root / f"{key}.json").write_text(json.dumps(campaign[key]))
    for key in ("rows", "conf"):
        (root / f"{key}.jsonl").write_text("\n".join(json.dumps(r) for r in campaign[key]))
    args = [command, "--design", str(root / "design.json"), "--results", str(root / "rows.jsonl")]
    if campaign["objs"] is not None:
        args += ["--objectives", str(root / "objs.json")]
    if command == "confirm":
        args += ["--confirmation", str(root / "conf.jsonl")]
        if contract:
            args += ["--contract", str(root / "contract.json")]
    rc = doe.main(args + list(extra))
    captured = capsys.readouterr()
    return rc, json.loads(captured.out) if captured.out else None, captured.err


def test_complete_attested_contract_can_promote(campaign, capsys):
    rc, out, err = run(campaign, capsys)
    assert rc == 0, err
    assert out["numerical_confirmed"] and out["promotion_ready"] and out["done"]
    assert out["recommendation"] == "ship"
    assert out["promotion_checks"]["provenance"] == "unverified_attestations"
    assert out["criteria"][0]["threshold_check"] == "point_estimate"


def test_legacy_confirmation_is_numerical_only(campaign, capsys):
    rc, out, _ = run(campaign, capsys, contract=False)
    assert rc == 0
    assert out["numerical_confirmed"]
    assert not out["promotion_ready"] and not out["done"]
    assert out["recommendation"] == "complete_promotion_contract"


@pytest.mark.parametrize("section,field,value", [
    (None, "schema_version", 2), (None, "schema_version", True),
    (None, "fixture_id", "other"), (None, "scorer_id", "other"),
    (None, "objectives", []), (None, "selection", "pareto"),
    ("candidate", "run_id", 0), ("candidate", "config_hash", "other"),
    ("candidate", "candidate_id", "other"), ("candidate", "run_id", True),
    ("split", "independent", False), ("split", "independent", "true"),
    ("split", "confirmation_id", "screening"), ("split", "screening_id", ""),
    ("measurement", "valid", False), ("measurement", "valid", 1),
    ("measurement", "evidence", ""), ("review", "approved", False),
    ("review", "approved", "true"), ("review", "reviewer", ""), ("review", "evidence", "")])
def test_incomplete_or_mismatched_contract_never_promotes(campaign, capsys, section, field, value):
    dest = campaign["contract"][section] if section else campaign["contract"]
    dest[field] = value
    rc, out, err = run(campaign, capsys)
    assert rc == 0, err
    assert out["numerical_confirmed"]
    assert not out["done"] and not out["promotion_ready"]
    assert out["promotion_checks"]["blockers"]


@pytest.mark.parametrize("location,field", [("conf", "candidate_id"), ("conf", "config_hash"),
    ("conf", "fixture_id"), ("conf", "scorer_id"), ("conf", "split_id"),
    ("conf", "run_id"), ("rows", "candidate_id"), ("rows", "config_hash"),
    ("rows", "fixture_id"), ("rows", "scorer_id"), ("rows", "split_id"),
    ("design", "candidate_id"), ("design", "config_hash")])
def test_measured_identity_must_match_contract(campaign, capsys, location, field):
    if location == "design":
        row = campaign["design"]["runs"][2]
    elif location == "rows":
        row = next(r for r in campaign["rows"] if r["run_id"] == 2)
    else:
        row = campaign["conf"][0]
    row[field] = 1 if field == "run_id" else "other"
    rc, out, err = run(campaign, capsys)
    assert rc == 0, err
    assert not out["done"]
    assert any(field in b for b in out["promotion_checks"]["blockers"])


@pytest.mark.parametrize("location", ["rows", "conf"])
def test_missing_execution_guard_blocks_promotion(campaign, capsys, location):
    for row in campaign[location]:
        row.pop("guard_ok")
    rc, out, err = run(campaign, capsys)
    assert rc == 0, err
    assert out["numerical_confirmed"] and not out["promotion_ready"]


def test_any_failed_confirmation_guard_blocks_promotion(campaign, capsys):
    campaign["conf"][0]["guard_ok"] = False
    rc, out, _ = run(campaign, capsys)
    assert rc == 0 and not out["done"]


def test_overlapping_items_override_independent_attestation(campaign, capsys):
    campaign["conf"][0]["item_ids"] = ["screen-0"]
    rc, out, _ = run(campaign, capsys)
    assert rc == 0 and not out["done"]
    assert "screening and confirmation item_ids overlap" in out["promotion_checks"]["blockers"]


@pytest.mark.parametrize("command", ["analyze", "confirm"])
@pytest.mark.parametrize("legacy", [True, False])
def test_one_failed_replicate_excludes_whole_cell(campaign, capsys, command, legacy):
    next(r for r in campaign["rows"] if r["run_id"] == 2)["guard_ok"] = False
    if legacy:
        campaign["objs"] = None
        for key in ("rows", "conf"):
            for row in campaign[key]:
                row["value"] = row.pop("values")["latency"]
    rc, out, err = run(campaign, capsys, command=command, contract=False)
    assert rc == 0, err
    assert out["best_run"] == 3
    if command == "confirm":
        assert not out["done"]


@pytest.mark.parametrize("command", ["analyze", "confirm"])
@pytest.mark.parametrize("legacy", [True, False])
def test_all_failed_guards_yields_no_best(campaign, capsys, command, legacy):
    for row in campaign["rows"]:
        row["guard_ok"] = False
    if legacy:
        campaign["objs"] = None
        for key in ("rows", "conf"):
            for row in campaign[key]:
                row["value"] = row.pop("values")["latency"]
    rc, out, err = run(campaign, capsys, command=command, contract=False)
    assert rc == 0, err
    assert out["best_run"] is None
    if command == "confirm":
        assert not out["done"] and not out["numerical_confirmed"]


@pytest.mark.parametrize("location", ["rows", "conf"])
@pytest.mark.parametrize("guard", ["false", None, 0, 1])
def test_malformed_guards_fail_parsing(campaign, capsys, location, guard):
    campaign[location][0]["guard_ok"] = guard
    rc, out, err = run(campaign, capsys)
    assert rc == 2 and out is None
    assert "guard_ok must be a boolean" in err


@pytest.mark.parametrize("value", [float("nan"), float("inf"), None, True, "78"])
def test_invalid_confirmation_measurements_fail(campaign, capsys, value):
    campaign["conf"][0]["values"]["latency"] = value
    rc, out, _ = run(campaign, capsys)
    assert rc == 2 and out is None


@pytest.mark.parametrize("alpha", ["nan", "inf", "0", "1", "-1"])
def test_invalid_alpha_fails(campaign, capsys, alpha):
    rc, out, err = run(campaign, capsys, extra=("--alpha", alpha))
    assert rc == 2 and out is None and "alpha" in err


def test_empty_confirmation_fails(campaign, capsys):
    campaign["conf"] = []
    rc, out, err = run(campaign, capsys)
    assert rc == 2 and out is None and "at least one" in err


def test_invalid_objectives_cannot_reach_confirmation(campaign, capsys):
    campaign["objs"][0]["direction"] = "sideways"
    rc, out, err = run(campaign, capsys)
    assert rc == 2 and out is None and "objectives contract error" in err


def test_missing_primary_bar_cannot_promote(campaign, capsys):
    campaign["objs"][0].pop("target")
    campaign["contract"]["objectives"] = copy.deepcopy(campaign["objs"])
    rc, out, _ = run(campaign, capsys)
    assert rc == 0 and out["numerical_confirmed"] and not out["promotion_ready"]


def test_cli_rejects_three_category_design(capsys):
    rc = doe.main(["generate", "--factors", json.dumps([
        {"name": "model", "levels": ["a", "b", "c"]},
        {"name": "mode", "levels": ["off", "on"]}])])
    output = capsys.readouterr()
    assert rc == 2 and not output.out
    assert "3+ level categorical designs are unsupported" in output.err


@pytest.mark.parametrize("status", ["disabled", "unavailable", "error", "unknown"])
@pytest.mark.parametrize("location", ["rows", "conf"])
def test_required_semantic_failure_cannot_promote(campaign, capsys, status, location):
    for row in campaign[location]:
        row["semantic_assessment"] = {"required": True, "status": status,
                                      "measurement_available": False}
    rc, out, err = run(campaign, capsys)
    assert rc == 0, err
    assert not out["done"] and not out["promotion_ready"]
    if location == "rows":
        assert out["best_run"] is None


@pytest.mark.parametrize("receipt", [None,
    {"required": False, "status": "ok", "measurement_available": False},
    {"required": False, "status": "ok", "measurement_available": 1},
    {"required": False, "status": "unknown", "measurement_available": True}])
def test_contract_required_semantic_cannot_use_absent_or_invalid_receipt(campaign, capsys, receipt):
    campaign["contract"]["semantic_assessor"] = {"required": True}
    for key in ("rows", "conf"):
        for row in campaign[key]:
            if receipt is not None:
                row["semantic_assessment"] = receipt
    rc, out, err = run(campaign, capsys)
    assert rc == 0, err
    assert out["numerical_confirmed"] and not out["done"]
    assert any("required semantic" in b for b in out["promotion_checks"]["blockers"])


def test_contract_required_semantic_valid_receipts_can_promote(campaign, capsys):
    campaign["contract"]["semantic_assessor"] = {"required": True}
    for key in ("rows", "conf"):
        for row in campaign[key]:
            row["semantic_assessment"] = {"required": True, "status": "ok",
                                          "measurement_available": True}
    rc, out, err = run(campaign, capsys)
    assert rc == 0, err
    assert out["done"]


@pytest.mark.parametrize("version", [None, "future", [], {}])
def test_unknown_contract_never_promotes(campaign, capsys, version):
    campaign["contract"] = version
    rc, out, err = run(campaign, capsys)
    assert rc == 0, err
    assert out["numerical_confirmed"] and not out["done"]


@pytest.mark.parametrize("command", ["analyze", "confirm"])
def test_result_run_id_must_be_integer(campaign, capsys, command):
    campaign["rows"][0]["run_id"] = 0.5
    rc, out, err = run(campaign, capsys, command=command)
    assert rc == 2 and out is None and "run_id" in err



def test_contract_objective_boolean_cannot_equal_numeric_threshold(campaign, capsys):
    campaign["objs"][0]["target"] = 1
    campaign["contract"]["objectives"][0]["target"] = True
    rc, out, err = run(campaign, capsys)
    assert rc == 0, err
    assert not out["promotion_checks"]["ready"]
    assert any("contract objectives" in b for b in out["promotion_checks"]["blockers"])


def test_reordered_design_binding_cannot_promote_wrong_factors(campaign, capsys):
    runs = campaign["design"]["runs"]
    runs[2], runs[3] = runs[3], runs[2]
    rc, out, err = run(campaign, capsys)
    assert rc == 0, err
    assert not out["done"]
    assert any("matrix index" in b for b in out["promotion_checks"]["blockers"])



@pytest.mark.parametrize("replacement", [{"a":999,"b":"arbitrary"}, {}, None, {"a":True,"b":"off"}])
def test_modified_factor_binding_cannot_promote(campaign, capsys, replacement):
    campaign["design"]["runs"][2]["_factors"] = replacement
    rc, out, err = run(campaign, capsys)
    assert rc == 0, err
    assert not out["done"]
    assert any("_factors" in b for b in out["promotion_checks"]["blockers"])


def test_legacy_design_without_levels_is_descriptive_only(campaign, capsys):
    campaign["design"]["factors"] = [{"name":f["name"]} for f in campaign["design"]["factors"]]
    rc, out, err = run(campaign, capsys)
    assert rc == 0, err
    assert out["numerical_confirmed"] and not out["promotion_ready"]
    assert any("factor binding" in b for b in out["promotion_checks"]["blockers"])


def test_duplicate_confirmation_attempt_rejected(campaign, capsys):
    campaign["conf"] = [campaign["conf"][1]] * 5
    rc, out, err = run(campaign, capsys)
    assert rc == 2 and out is None
    assert "duplicate confirmation attempt_id" in err


def test_confirmation_without_attempt_identity_is_descriptive_only(campaign, capsys):
    for row in campaign["conf"]:
        row.pop("attempt_id")
    rc, out, err = run(campaign, capsys)
    assert rc == 0, err
    assert out["numerical_confirmed"] and not out["done"]
    assert any("cannot be deduplicated" in w for w in out["warnings"])


@pytest.mark.parametrize("matrix", [[], [[]], [1, 2], [[1, 0], [-1, 1]]])
@pytest.mark.parametrize("command", ["analyze", "confirm"])
def test_invalid_matrix_is_clean_input_error(campaign, capsys, matrix, command):
    campaign["design"]["matrix"] = matrix
    rc, out, err = run(campaign, capsys, command=command)
    assert rc == 2 and out is None and "design matrix" in err


def test_overflow_measurement_is_clean_input_error(campaign, capsys):
    campaign["conf"][0]["values"]["latency"] = 10 ** 400
    rc, out, err = run(campaign, capsys)
    assert rc == 2 and out is None and "finite range" in err



def test_failed_nonselected_screening_cannot_supply_confirmation_variance(campaign, capsys):
    campaign["rows"][0]["guard_ok"] = False
    campaign["rows"][0]["values"]["latency"] = 10000
    rc, out, err = run(campaign, capsys)
    assert rc == 0, err
    assert out["best_run"] == 2
    assert not out["numerical_confirmed"] and not out["promotion_ready"]
    assert out["numerical_guard_failures"] == ["screening row 0"]
    assert any("contaminate" in w for w in out["warnings"])


def test_unknown_nonselected_screening_guard_blocks_promotion(campaign, capsys):
    campaign["rows"][0].pop("guard_ok")
    rc, out, err = run(campaign, capsys)
    assert rc == 0, err
    assert out["numerical_confirmed"] and not out["promotion_ready"]
    assert any("every screening guard" in b for b in out["promotion_checks"]["blockers"])


def test_failed_confirmation_guard_prevents_numerical_confirmation(campaign, capsys):
    campaign["conf"][0]["guard_ok"] = False
    rc, out, err = run(campaign, capsys)
    assert rc == 0, err
    assert not out["numerical_confirmed"] and not out["done"]
    assert out["numerical_guard_failures"] == ["confirmation row 0"]


def test_confirmation_reports_near_tie_selection(campaign, capsys):
    for row in campaign["rows"]:
        if row["run_id"] == 3:
            row["values"]["latency"] -= 3.99
    rc, out, err = run(campaign, capsys)
    assert rc == 0, err
    assert out["contenders"] == [2, 3]
    assert any("tie" in w for w in out["warnings"])



def test_cross_split_attempt_identity_reuse_blocks_promotion(campaign, capsys):
    campaign["rows"][0]["attempt_id"] = campaign["conf"][0]["attempt_id"]
    rc, out, err = run(campaign, capsys)
    assert rc == 0, err
    assert out["numerical_confirmed"] and not out["promotion_ready"]
    assert "screening and confirmation attempt_ids overlap" in out["promotion_checks"]["blockers"]


@pytest.mark.parametrize("value", [["latency"], "latency", 78, None])
def test_confirmation_values_require_object(campaign, capsys, value):
    campaign["conf"][0]["values"] = value
    rc, out, err = run(campaign, capsys)
    assert rc == 2 and out is None and "values must be an object" in err


@pytest.mark.parametrize("bad_contract", ["{bad-json", "missing-contract.json"])
def test_contract_input_error_is_clean_for_direct_command(campaign, capsys, bad_contract):
    import argparse
    run(campaign, capsys)  # write the valid fixture files
    root = campaign["root"]
    args = argparse.Namespace(design=str(root/"design.json"),results=str(root/"rows.jsonl"),
        confirmation=str(root/"conf.jsonl"),objectives=str(root/"objs.json"),
        alpha=.05,contract=bad_contract,selection=None)
    assert doe.cmd_confirm(args) == 2
    captured = capsys.readouterr()
    assert not captured.out and "--contract parse error" in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parametrize("direction,bar,values", [
    ("higher", .9, [.5,1,1,1,1]), ("lower",10,[14,9,9,9,9])])
def test_guardrail_reports_individual_breaches_without_redefining_mean_rule(campaign, capsys,direction,bar,values):
    obj={"name":"guard","role":"guardrail","direction":direction,"min_acceptable":bar}
    campaign["objs"].append(obj)
    campaign["contract"]["objectives"] = copy.deepcopy(campaign["objs"])
    for row in campaign["rows"]:
        row["values"]["guard"] = (.94+.01*row["run_id"] if direction=="higher" else 8-.1*row["run_id"])
    for row,value in zip(campaign["conf"],values):
        row["values"]["guard"]=value
    rc,out,err=run(campaign,capsys)
    assert rc==0,err
    assert out["done"] and out["threshold_scope"]=="confirmation_mean"
    criterion=next(c for c in out["criteria"] if c["name"]=="guard")
    assert criterion["pass"] and criterion["individual_breach_count"]==1
    assert criterion["threshold_scope"]=="confirmation_mean"
    assert any("1 of 5 confirmation observations breach" in w for w in out["warnings"])


def test_zero_min_effect_remains_allowed_and_warns_no_positive_improvement(campaign,capsys):
    campaign["objs"][0].pop("target")
    campaign["objs"][0].update(baseline=78,min_effect=0)
    campaign["contract"]["objectives"]=copy.deepcopy(campaign["objs"])
    rc,out,err=run(campaign,capsys)
    assert rc==0,err
    assert out["done"]
    assert any("min_effect=0 declares no positive improvement requirement" in w for w in out["warnings"])
