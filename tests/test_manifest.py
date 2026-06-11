# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Plugin manifest + command-surface tests (cross-host)."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ManifestTests(unittest.TestCase):
    def test_codex_and_claude_manifests_match_core_fields(self) -> None:
        codex = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text())
        claude = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
        for field in ("name", "version", "description", "license"):
            self.assertEqual(codex[field], claude[field], f"mismatch on {field}")

    def test_required_plugin_paths_exist(self) -> None:
        for manifest_name in (".codex-plugin", ".claude-plugin"):
            manifest = json.loads((ROOT / manifest_name / "plugin.json").read_text())
            for field in ("commands", "agents", "skills"):
                path = ROOT / manifest[field]
                self.assertTrue(path.exists(), f"{manifest_name} {field} path missing: {path}")

    def test_advertised_commands_and_agents_present(self) -> None:
        for cmd in ("agent-doe", "status", "doe"):
            self.assertTrue((ROOT / "commands" / f"{cmd}.md").exists(), f"command missing: {cmd}")
        for agent in ("optimize-runner", "overfitting-reviewer"):
            self.assertTrue((ROOT / "agents" / f"{agent}.md").exists(), f"agent missing: {agent}")
        self.assertTrue((ROOT / "skills" / "agent-doe-engine" / "SKILL.md").exists())


class CommandSurfaceTests(unittest.TestCase):
    def test_summary_without_active_experiment_is_clean_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "loop.py"), "--summary", "--workdir", tmpdir],
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(result.stdout)
            self.assertFalse(data["active"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
