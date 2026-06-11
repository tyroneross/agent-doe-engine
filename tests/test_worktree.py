#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for worktree.py - init, info, cleanup, slug normalization, reuse paths.

Each test creates a real throw-away git repo in a temp dir and exercises
the helper end-to-end. Stdlib only.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
import worktree  # noqa: E402

SCRIPT = SCRIPTS_DIR / "worktree.py"


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.setdefault("GIT_AUTHOR_NAME", "test")
    env.setdefault("GIT_AUTHOR_EMAIL", "test@example.com")
    env.setdefault("GIT_COMMITTER_NAME", "test")
    env.setdefault("GIT_COMMITTER_EMAIL", "test@example.com")
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=env,
    )


def _make_repo(tmpdir: Path, name: str = "demo") -> Path:
    repo = tmpdir / name
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("# demo\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


class SlugifyTests(unittest.TestCase):
    def test_simple(self) -> None:
        self.assertEqual(worktree.slugify("build-time"), "build-time")

    def test_spaces_and_uppercase(self) -> None:
        self.assertEqual(worktree.slugify("Latency AND Cost"), "latency-and-cost")

    def test_special_chars_collapsed(self) -> None:
        self.assertEqual(worktree.slugify("foo!!!bar...baz"), "foo-bar-baz")

    def test_empty_falls_back_to_run(self) -> None:
        self.assertEqual(worktree.slugify("!!!"), "run")

    def test_long_truncated(self) -> None:
        s = worktree.slugify("a" * 100)
        self.assertEqual(len(s), 40)


class InitTests(unittest.TestCase):
    def test_init_creates_worktree_and_branch(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            repo = _make_repo(tdp)
            ref = worktree.WorktreeRef(repo_root=repo, slug="build-time")
            r = worktree.init_worktree(ref)
            self.assertTrue(r["created"])
            self.assertEqual(r["branch"], "agent-doe-engine/build-time")
            self.assertTrue(Path(r["path"]).is_dir())
            self.assertTrue((Path(r["path"]) / "README.md").exists())

    def test_init_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            repo = _make_repo(tdp)
            ref = worktree.WorktreeRef(repo_root=repo, slug="reuse")
            worktree.init_worktree(ref)
            r2 = worktree.init_worktree(ref)  # second call must not error
            self.assertFalse(r2["created"])
            self.assertIn("already exists", r2["note"])

    def test_init_reuses_branch_after_cleanup(self) -> None:
        """Cleanup that keeps the branch (default) must let init reuse it."""
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            repo = _make_repo(tdp)
            ref = worktree.WorktreeRef(repo_root=repo, slug="keep-branch")
            worktree.init_worktree(ref)
            worktree.cleanup_worktree(ref)  # branch kept
            r = worktree.init_worktree(ref)
            self.assertTrue(r["created"])
            self.assertIn("reused existing branch", r["note"])


class InfoTests(unittest.TestCase):
    def test_info_on_uninitialized_reports_absent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            repo = _make_repo(tdp)
            ref = worktree.WorktreeRef(repo_root=repo, slug="absent")
            r = worktree.info_worktree(ref)
            self.assertFalse(r["exists"])

    def test_info_on_initialized_reports_present(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            repo = _make_repo(tdp)
            ref = worktree.WorktreeRef(repo_root=repo, slug="here")
            worktree.init_worktree(ref)
            r = worktree.info_worktree(ref)
            self.assertTrue(r["exists"])
            self.assertEqual(r["branch"], "agent-doe-engine/here")


class CleanupTests(unittest.TestCase):
    def test_cleanup_removes_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            repo = _make_repo(tdp)
            ref = worktree.WorktreeRef(repo_root=repo, slug="gone")
            worktree.init_worktree(ref)
            r = worktree.cleanup_worktree(ref)
            self.assertTrue(r["removed"])
            self.assertFalse(Path(r["path"]).exists())

    def test_cleanup_with_delete_branch(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            repo = _make_repo(tdp)
            ref = worktree.WorktreeRef(repo_root=repo, slug="drop")
            worktree.init_worktree(ref)
            r = worktree.cleanup_worktree(ref, delete_branch=True)
            self.assertTrue(r["removed"])
            self.assertTrue(r["branch_deleted"])
            self.assertFalse(worktree.branch_exists(repo, "agent-doe-engine/drop"))

    def test_cleanup_on_absent_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            repo = _make_repo(tdp)
            ref = worktree.WorktreeRef(repo_root=repo, slug="never")
            r = worktree.cleanup_worktree(ref)
            self.assertFalse(r["removed"])


class CliTests(unittest.TestCase):
    def test_cli_init_info_cleanup_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            repo = _make_repo(tdp)

            r = subprocess.run(
                [sys.executable, str(SCRIPT), "--workdir", str(repo),
                 "--target", "build-time", "--json", "init"],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            init_out = json.loads(r.stdout)
            self.assertEqual(init_out["branch"], "agent-doe-engine/build-time")

            r = subprocess.run(
                [sys.executable, str(SCRIPT), "--workdir", str(repo),
                 "--target", "build-time", "--json", "info"],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(r.returncode, 0)
            self.assertTrue(json.loads(r.stdout)["exists"])

            r = subprocess.run(
                [sys.executable, str(SCRIPT), "--workdir", str(repo),
                 "--target", "build-time", "--json", "cleanup", "--delete-branch"],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(r.returncode, 0)
            self.assertTrue(json.loads(r.stdout)["removed"])

    def test_cli_info_absent_exits_1(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            repo = _make_repo(tdp)
            r = subprocess.run(
                [sys.executable, str(SCRIPT), "--workdir", str(repo),
                 "--target", "missing", "info"],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(r.returncode, 1)
            self.assertIn("not initialized", r.stdout)

    def test_cli_help_does_not_crash(self) -> None:
        for sub in ("init", "info", "cleanup"):
            r = subprocess.run(
                [sys.executable, str(SCRIPT), "--target", "x", sub, "--help"],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(r.returncode, 0, msg=f"{sub} --help failed: {r.stderr}")


if __name__ == "__main__":
    unittest.main()
