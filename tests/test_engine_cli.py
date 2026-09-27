# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Exercise the host-neutral process boundary from a consumer directory."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "engine_cli.py"
COMMANDS = ("doe", "analyst", "paired", "ledger", "report", "assess", "loop",
            "metric", "suggest-factors", "validate-factors", "worktree")


def invoke(tmp_path, *args):
    return subprocess.run([sys.executable, str(CLI), *args], cwd=tmp_path,
                          capture_output=True, text=True, timeout=15)


@pytest.mark.parametrize("command", COMMANDS)
def test_command_help_outside_repo(tmp_path, command):
    result = invoke(tmp_path, command, "--help")
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout


def test_json_argument_forwarding(tmp_path):
    factors = [{"name": "workers", "low": 1, "high": 4},
               {"name": "batch", "low": 8, "high": 32}]
    args = ["generate", "--factors", json.dumps(factors), "--seed", "7"]
    actual = invoke(tmp_path, "doe", *args)
    direct = subprocess.run([sys.executable, str(ROOT / "scripts/doe.py"), *args],
                            cwd=tmp_path, capture_output=True, text=True, timeout=15)
    assert actual.returncode == direct.returncode == 0
    assert json.loads(actual.stdout) == json.loads(direct.stdout)


def test_guard_failure_exit_code_and_json_preserved(tmp_path):
    result = invoke(tmp_path, "metric", "--guard", "exit 7")
    assert result.returncode == 1
    assert json.loads(result.stdout)["passed"] is False


def test_module_invocation_uses_engine_code_not_consumer_names(tmp_path):
    for module in ("doe", "objectives", "doe_stats"):
        (tmp_path / (module + ".py")).write_text("raise RuntimeError('consumer module executed')\n")
    result = subprocess.run([sys.executable, "-m", "engine_cli", "doe", "detect", "4"],
                            cwd=tmp_path, env={**os.environ, "PYTHONPATH": str(CLI.parent)},
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert isinstance(json.loads(result.stdout), dict)


def test_error_and_unknown_command_rejected(tmp_path):
    result = invoke(tmp_path, "doe", "generate", "--factors", "not json")
    assert result.returncode != 0
    assert result.stderr
    unknown = invoke(tmp_path, "arbitrary_module")
    assert unknown.returncode == 2
    assert "invalid choice" in unknown.stderr


def test_report_stylesheet_and_output_from_consumer_directory(tmp_path):
    legacy = tmp_path / "rows.jsonl"
    legacy.write_text(json.dumps({"run_id": 0, "values": {"score": 3}, "guard_ok": True}) + "\n")
    output = tmp_path / "report"
    result = invoke(tmp_path, "report", "--legacy", str(legacy), "--output", str(output))
    assert result.returncode == 0, result.stderr
    html = (output / "report.html").read_text()
    assert "<style>" in html and "@media" in html
    assert (output / "report.csv").exists()
    assert (output / "report.md").exists()
