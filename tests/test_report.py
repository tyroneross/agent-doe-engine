# SPDX-License-Identifier: Apache-2.0
import csv
import io
import json
import re
import shutil
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import experiment_ledger as ledger
import report


@pytest.fixture
def model(tmp_path):
    path = tmp_path / "campaign.jsonl"
    hostile = '</script><script>alert("x")</script>'
    ledger.init_campaign(path, {"campaign_id": "fixture", "title": "Fixture " + hostile, "plan": {"method": "deterministic fixture"}})
    for index, (guard, error, metrics) in enumerate([(True, None, {"score": 1.0, "ms": 25}), (False, "guard failed", {}), (None, None, {"score": None})]):
        ledger.append_attempt(path, {"attempt_id": f"a{index}", "batch_id": "b1", "cell_id": f"c{index}", "phase": "screening" if index < 2 else "confirmation", "settings": {"prompt": hostile}, "change": "=SUM(1,1)", "metrics": metrics,
                                    "measurements": {"score": {"command": "printf 1", "method": "exact-match", "unit": "fraction", "n": 3}}, "guard_ok": guard, "error": error})
    ledger.append_decision(path, {"decision_id": "d1", "attempt_ids": ["a0", "a1"], "reason": "Guard failure requires review", "next_change": "Repeat failed cell", "evidence": hostile})
    return report.load_report(ledger=path)


def test_counts_and_exports_preserve_all_attempts_and_metrics(model):
    assert [report.status(a) for a in model["attempts"]] == ["complete", "failed", "incomplete"]
    rows = list(csv.DictReader(io.StringIO(report.render_csv(model))))
    assert len(rows) == 4
    assert {r["attempt_id"] for r in rows} == {"a0", "a1", "a2"}
    score = next(row for row in rows if row["attempt_id"] == "a0" and row["metric"] == "score")
    assert score["n"] == "3" and score["method"] == "exact-match"
    assert rows[0]["change"] == "'=SUM(1,1)"
    assert rows[2]["guard"] == "Failed" and rows[2]["result"] == "Missing"
    assert rows[3]["guard"] == "Not recorded"
    assert json.loads(rows[0]["evidence"])["decisions"][0]["decision_id"] == "d1"
    markdown = report.render_markdown(model)
    assert "printf 1" in markdown and "exact-match" in markdown
    assert "</script>" not in markdown and "Repeat failed cell" in markdown


class Scripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []
        self.headings = 0
        self.external = []
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script":
            self.scripts.append(attrs)
        if tag == "h1":
            self.headings += 1
        if attrs.get("src") or (tag == "link" and attrs.get("href")):
            self.external.append(attrs)


def test_html_escapes_evidence_and_has_offline_working_controls(model):
    page = report.render_html(model)
    parser = Scripts()
    parser.feed(page)
    assert len(parser.scripts) == 2 and parser.headings == 1
    assert not parser.external
    assert '<script>alert("x")</script>' not in page
    assert "\\u003c/script\\u003e" in page
    assert 'download="report.csv"' in page and 'download="report.md"' in page
    assert "addEventListener('change'" in page and "URL.createObjectURL" in page
    assert "1 failed and 1 incomplete" in page
    assert "min-height:44px" in page and 'tabindex="0"' in page
    assert "prefers-reduced-motion" in page


def test_legacy_is_explicit_and_never_invents_measurement_provenance(tmp_path):
    path = tmp_path / "legacy.jsonl"
    path.write_text(json.dumps({"_run_id": "run0", "split": "dev", "n": 31, "criteria_style": "plain", "subcat_accuracy": 0.8387, "errors": 0}) + "\n")
    model = report.load_report(legacy=path)
    assert model["legacy"] and "Legacy" in model["source_label"]
    attempt = model["attempts"][0]
    assert attempt["settings"] == {"criteria_style": "plain"}
    assert attempt["metrics"] == {"subcat_accuracy": 0.8387}
    assert attempt["guard_ok"] is None
    row = report.export_rows(model)[0]
    assert row[9:13] == ["Not recorded", 31, "Not recorded", "Not recorded"] or row[9:13] == ["Not recorded", "31", "Not recorded", "Not recorded"]
    page = report.render_html(model)
    assert "Execution order is not inferred" in page
    assert "Campaign recorded: Not recorded" in page


@pytest.mark.parametrize("content", ['{}\n{}\n', '{"score":NaN}\n', '{"score":Infinity}\n', '[]\n', '{"values":{"x":"bad"}}\n', '{"guard_ok":"false"}\n', '{'])
def test_malformed_legacy_is_rejected(tmp_path, content):
    if content == '{}\n{}\n':
        # Missing IDs receive explicit source-line identities, never execution IDs.
        path = tmp_path / "legacy.jsonl"
        path.write_text(content)
        assert report.load_report(legacy=path)["attempts"][1]["attempt_id"] == "source-line-2"
        return
    path = tmp_path / "legacy.jsonl"
    path.write_text(content)
    with pytest.raises(ValueError, match="Legacy line"):
        report.load_report(legacy=path)


def test_empty_and_cli_exports(tmp_path, capsys):
    path = tmp_path / "empty.jsonl"
    path.write_text("")
    model = report.load_report(legacy=path)
    assert "No attempts recorded" in report.render_html(model)
    report.main(["--legacy", str(path), "--output", str(tmp_path / "out")])
    outputs = json.loads(capsys.readouterr().out)
    assert all(Path(p).is_file() for p in outputs.values())
    assert set(outputs) == {"html", "csv", "md"}


def test_source_choice_required():
    with pytest.raises(ValueError, match="exactly one"):
        report.load_report()


def test_legacy_replicates_preserve_cell_and_unique_attempts(tmp_path):
    path = tmp_path / "replicates.jsonl"
    path.write_text('{"run_id":0,"values":{"score":1}}\n{"run_id":0,"values":{"score":0}}\n')
    model = report.load_report(legacy=path)
    assert [a["attempt_id"] for a in model["attempts"]] == ["source-line-1", "source-line-2"]
    assert [a["cell_id"] for a in model["attempts"]] == ["0", "0"]
    assert len(report.export_rows(model)) == 2


def test_legacy_preserves_measurement_metadata_and_falls_back_only_for_missing_n(tmp_path):
    raw = {"run_id": 0, "n": 31, "values": {"score": 0.8, "latency": 15, "cost": 0.2},
           "measurements": {
               "score": {"unit": "fraction", "method": "exact-match", "command": "python score.py", "n": 20},
               "latency": {"unit": "ms", "method": "monotonic clock", "command": "python time.py"},
               "cost": {"unit": "USD", "n": None},
               "unmeasured": {"unit": "tokens", "n": 0}}}
    path = tmp_path / "legacy.jsonl"
    path.write_text(json.dumps(raw) + "\n")
    model = report.load_report(legacy=path)
    measurements = model["attempts"][0]["measurements"]
    assert measurements["score"] == raw["measurements"]["score"]
    assert measurements["latency"]["n"] == 31
    assert measurements["cost"]["n"] is None
    assert measurements["unmeasured"]["n"] == 0
    rows = {r["metric"]: r for r in csv.DictReader(io.StringIO(report.render_csv(model)))}
    assert [rows["score"][k] for k in ("unit", "method", "command", "n")] == ["fraction", "exact-match", "python score.py", "20"]
    assert rows["latency"]["n"] == "31"
    assert rows["cost"]["n"] == "Not recorded"
    assert rows["unmeasured"]["result"] == "Missing"
    assert model["attempts"][0]["source_record"] == raw
    for output in (report.render_html(model), report.render_markdown(model)):
        assert "python score.py" in output and "fraction" in output and "monotonic clock" in output


@pytest.mark.parametrize("metadata", [None, [], {"score": None}, {"score": []},
    {"score": {"unit": 1}}, {"score": {"method": {}}}, {"score": {"command": False}},
    {"score": {"n": -1}}, {"score": {"n": True}}, {"score": {"n": 1.5}}, {"score": {"n": "3"}}])
def test_legacy_rejects_malformed_measurements(tmp_path, metadata):
    path = tmp_path / "legacy.jsonl"
    path.write_text(json.dumps({"values": {"score": 1}, "measurements": metadata}) + "\n")
    with pytest.raises(ValueError, match="Legacy line 1"):
        report.load_report(legacy=path)


@pytest.mark.parametrize("n", [-1, True, "3", 2.5])
def test_legacy_rejects_malformed_fallback_n(tmp_path, n):
    path = tmp_path / "legacy.jsonl"
    path.write_text(json.dumps({"values": {"score": 1}, "n": n}) + "\n")
    with pytest.raises(ValueError, match="top-level n"):
        report.load_report(legacy=path)


@pytest.mark.parametrize("receipt", [{"required": True, "status": "error", "measurement_available": False}, {"required": True, "status": "ok", "measurement_available": False}, {"required": True, "status": "disabled"}])
def test_required_semantic_missing_cannot_be_complete(model, receipt):
    model["attempts"][0]["semantic_assessment"] = receipt
    assert report.status(model["attempts"][0]) == "incomplete"
    assert "Required semantic measurement unavailable" in report.render_html(model)
    assert "Required semantic measurement unavailable" in report.render_csv(model)


def test_generated_javascript_parses(model, tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is needed only for generated JavaScript syntax validation")
    page = report.render_html(model)
    script = re.search(r"<script>(.*?)</script>", page, flags=re.S).group(1)
    path = tmp_path / "report.js"
    path.write_text(script)
    result = subprocess.run([node, "--check", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
