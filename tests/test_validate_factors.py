#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for validate_factors.py — adjustability classification + mutation probe.

Tests cover every classification branch:
  adjustable                       — happy path, integer constant, real refs
  adjustable (env_getenv)          — Python env var with numeric default
  adjustable (env_process)         — JS process.env with ||-numeric fallback
  not_adjustable / dead_constant   — defined but zero references
  not_adjustable / duplicate_definition — two sites, different values
  not_adjustable / no_definition_site   — name not present
  byte_revert_integrity            — file bytes restored verbatim post-probe
  cli_round_trip                   — suggest_factors-shaped JSON in, validated JSON out
  reject_non_adjustable_exit_code  — --reject-non-adjustable returns 1 on failure
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
import validate_factors  # noqa: E402

SCRIPT = SCRIPTS_DIR / "validate_factors.py"


def _write(root: Path, rel: str, body: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


class AdjustableTests(unittest.TestCase):
    """Happy-path: candidate is real, references exist, probe round-trips."""

    def test_upper_snake_python_adjustable(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "config.py", "BATCH_SIZE = 32\n")
            _write(
                root, "worker.py",
                "from config import BATCH_SIZE\n\n"
                "def run():\n"
                "    return BATCH_SIZE * 2\n",
            )
            v = validate_factors.classify_candidate("BATCH_SIZE", 32.0, root)
            self.assertEqual(v.adjustability, "adjustable")
            self.assertEqual(v.reason, "ok")
            self.assertIn("reference", v.evidence)
            # File must be byte-identical to the original after probe
            self.assertEqual(
                (root / "config.py").read_text(),
                "BATCH_SIZE = 32\n",
            )

    def test_upper_snake_typescript_adjustable(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(
                root, "src/config.ts",
                "export const TIMEOUT_MS = 5000;\n",
            )
            _write(
                root, "src/use.ts",
                "import { TIMEOUT_MS } from './config';\nconst t = TIMEOUT_MS + 1;\n",
            )
            v = validate_factors.classify_candidate("TIMEOUT_MS", 5000.0, root)
            self.assertEqual(v.adjustability, "adjustable")
            self.assertEqual(
                (root / "src/config.ts").read_text(),
                "export const TIMEOUT_MS = 5000;\n",
            )

    def test_env_getenv_python_adjustable(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(
                root, "app.py",
                "import os\nWORKERS = int(os.getenv('WORKERS', '4'))\n"
                "for _ in range(WORKERS): pass\n",
            )
            _write(root, "consumer.py", "from app import WORKERS\nprint(WORKERS)\n")
            v = validate_factors.classify_candidate("WORKERS", 4.0, root)
            self.assertEqual(v.adjustability, "adjustable")
            # Original bytes restored
            self.assertIn("os.getenv('WORKERS', '4')", (root / "app.py").read_text())

    def test_env_process_js_adjustable(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(
                root, "server.js",
                "const PORT = process.env.PORT || 3000;\nconsole.log(PORT);\n",
            )
            _write(root, "client.js", "import { PORT } from './server';\n")
            v = validate_factors.classify_candidate("PORT", 3000.0, root)
            self.assertEqual(v.adjustability, "adjustable")
            self.assertIn("process.env.PORT || 3000", (root / "server.js").read_text())

    def test_float_constant_adjustable(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "model.py", "DROPOUT = 0.2\n")
            _write(root, "train.py", "from model import DROPOUT\nx = DROPOUT * 0.5\n")
            v = validate_factors.classify_candidate("DROPOUT", 0.2, root)
            self.assertEqual(v.adjustability, "adjustable")
            self.assertEqual((root / "model.py").read_text(), "DROPOUT = 0.2\n")


class NotAdjustableTests(unittest.TestCase):
    def test_dead_constant(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "config.py", "UNUSED_LIMIT = 100\n")
            v = validate_factors.classify_candidate("UNUSED_LIMIT", 100.0, root)
            self.assertEqual(v.adjustability, "not_adjustable")
            self.assertEqual(v.reason, "dead_constant")
            self.assertIn("zero references", v.evidence)

    def test_duplicate_definition(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "a.py", "MAX_RETRY = 3\nprint(MAX_RETRY)\n")
            _write(root, "b.py", "MAX_RETRY = 5\nprint(MAX_RETRY)\n")
            v = validate_factors.classify_candidate("MAX_RETRY", 3.0, root)
            self.assertEqual(v.adjustability, "not_adjustable")
            self.assertEqual(v.reason, "duplicate_definition")
            self.assertIn("conflicting values", v.evidence)

    def test_no_definition_site(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "irrelevant.py", "x = 1\n")
            v = validate_factors.classify_candidate("DOES_NOT_EXIST", 7.0, root)
            self.assertEqual(v.adjustability, "not_adjustable")
            self.assertEqual(v.reason, "no_definition_site")


class ByteRevertIntegrityTests(unittest.TestCase):
    """Mutation probe must restore the original bytes verbatim — including
    line endings and trailing whitespace."""

    def test_crlf_line_endings_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            original = b"BATCH = 8\r\nREF = BATCH\r\n"
            (root / "x.py").write_bytes(original)
            _write(root, "use.py", "from x import BATCH\nprint(BATCH)\n")
            v = validate_factors.classify_candidate("BATCH", 8.0, root)
            self.assertEqual(v.adjustability, "adjustable")
            self.assertEqual((root / "x.py").read_bytes(), original)

    def test_no_trailing_newline_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            original = b"LIMIT = 50"  # no \n at end
            (root / "x.py").write_bytes(original)
            _write(root, "use.py", "from x import LIMIT\n")
            v = validate_factors.classify_candidate("LIMIT", 50.0, root)
            self.assertEqual(v.adjustability, "adjustable")
            self.assertEqual((root / "x.py").read_bytes(), original)


class CliTests(unittest.TestCase):
    def test_cli_round_trip_with_candidates_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "config.py", "BATCH_SIZE = 16\n")
            _write(root, "use.py", "from config import BATCH_SIZE\nprint(BATCH_SIZE)\n")
            _write(root, "dead.py", "UNUSED_KNOB = 99\n")
            candidates = [
                {"name": "BATCH_SIZE", "current_value": 16, "suggested_levels": [8, 16, 32],
                 "confidence": "high", "why": "tunable", "file": "config.py", "line": 1},
                {"name": "UNUSED_KNOB", "current_value": 99, "suggested_levels": [50, 99, 200],
                 "confidence": "low", "why": "no refs", "file": "dead.py", "line": 1},
            ]
            cf = root / "candidates.json"
            cf.write_text(json.dumps(candidates))
            r = subprocess.run(
                [sys.executable, str(SCRIPT), "--workdir", str(root),
                 "--candidates", str(cf), "--json"],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            out = json.loads(r.stdout)
            by_name = {row["name"]: row for row in out}
            self.assertEqual(by_name["BATCH_SIZE"]["adjustability"], "adjustable")
            self.assertEqual(by_name["UNUSED_KNOB"]["adjustability"], "not_adjustable")
            self.assertEqual(by_name["UNUSED_KNOB"]["reason"], "dead_constant")
            # Forwarded extras
            self.assertEqual(by_name["BATCH_SIZE"]["suggested_levels"], [8, 16, 32])
            self.assertIn("why", by_name["BATCH_SIZE"])

    def test_cli_reject_non_adjustable_exits_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "dead.py", "UNUSED_KNOB = 99\n")
            candidates = [{"name": "UNUSED_KNOB", "current_value": 99}]
            cf = root / "candidates.json"
            cf.write_text(json.dumps(candidates))
            r = subprocess.run(
                [sys.executable, str(SCRIPT), "--workdir", str(root),
                 "--candidates", str(cf), "--json", "--reject-non-adjustable"],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(r.returncode, 1)

    def test_cli_help_does_not_crash(self) -> None:
        r = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(r.returncode, 0)
        self.assertIn("--candidates", r.stdout)
        self.assertIn("--reject-non-adjustable", r.stdout)


if __name__ == "__main__":
    unittest.main()
