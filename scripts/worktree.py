#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""multi-goal worktree helper — every run lives in its own dedicated worktree.

Stdlib only.

Multi-goal must NEVER mutate the user's primary checkout of the target
repo. DOE applies + reverts factor values across many runs; even with a
clean revert, leaving the work in `main` (or whatever branch the user
was on) risks dirtying state, intermixing commits, and trashing
in-progress work.

This helper exposes three subcommands the skill calls:

    init    Create (or reuse) the worktree at <repo>-multigoal-<slug>
            on branch `multigoal/<slug>`. Prints the worktree path
            (the host LLM should `cd` there before running the rest of
            the multi-goal flow).
    info    Print the resolved worktree path and branch for <slug>
            (returns exit 1 if not initialized).
    cleanup Remove the worktree (and optionally its branch) after the
            run is done.

The slug is the target name the user supplied (e.g. "build-time",
"latency-and-cost"). Slugs are normalized to lowercase kebab-case.

This helper does NOT change directory itself — it prints the path so
the host LLM can issue an explicit `cd` in its next tool call. Helper
side-effects stay confined to `git worktree` operations.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


def _run(cmd: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, check=check, capture_output=True, text=True)


def slugify(name: str) -> str:
    """Normalize a target name to a worktree-safe slug.

    Lowercase, replace any run of non-alphanumeric chars with a single `-`,
    strip leading/trailing dashes, cap at 40 chars.
    """
    s = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
    if not s:
        s = "run"
    return s[:40]


@dataclass
class WorktreeRef:
    repo_root: Path
    slug: str

    def __post_init__(self) -> None:
        # Resolve so symlinks (e.g. macOS /var → /private/var) match what
        # `git worktree list` reports.
        self.repo_root = self.repo_root.resolve()

    @property
    def branch(self) -> str:
        return f"multigoal/{self.slug}"

    @property
    def path(self) -> Path:
        # Sibling directory: `<repo-name>-multigoal-<slug>` alongside the source repo
        return (self.repo_root.parent / f"{self.repo_root.name}-multigoal-{self.slug}").resolve()


def _resolve(p: str | Path) -> Path:
    """Best-effort path resolution that survives non-existent paths."""
    pp = Path(p)
    try:
        return pp.resolve()
    except OSError:
        return pp


def resolve_repo_root(workdir: Path) -> Path:
    """Resolve the git repo root for `workdir`. Raises if not a git repo."""
    try:
        r = _run(["git", "rev-parse", "--show-toplevel"], cwd=workdir)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"{workdir} is not inside a git repo: {e.stderr.strip()}") from e
    return Path(r.stdout.strip())


def existing_worktrees(repo_root: Path) -> list[dict]:
    """Return the parsed `git worktree list --porcelain` output."""
    r = _run(["git", "worktree", "list", "--porcelain"], cwd=repo_root)
    out: list[dict] = []
    current: dict = {}
    for line in r.stdout.splitlines():
        if not line.strip():
            if current:
                out.append(current)
                current = {}
            continue
        key, _, val = line.partition(" ")
        current[key] = val
    if current:
        out.append(current)
    return out


def branch_exists(repo_root: Path, branch: str) -> bool:
    r = _run(["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
             cwd=repo_root, check=False)
    return r.returncode == 0


def init_worktree(ref: WorktreeRef, base_ref: str = "HEAD") -> dict:
    """Create the worktree if it doesn't already exist; reuse if it does.

    Returns a dict with `path`, `branch`, `created` (bool), and `note`.
    """
    # Already present?
    for wt in existing_worktrees(ref.repo_root):
        if _resolve(wt.get("worktree", "")) == ref.path:
            return {
                "path": str(ref.path),
                "branch": ref.branch,
                "created": False,
                "note": f"worktree already exists at {ref.path}",
            }

    if ref.path.exists():
        raise RuntimeError(
            f"path {ref.path} already exists but is not a registered worktree — "
            "remove it or pick a different slug"
        )

    # Branch already exists (e.g. from a prior cleaned-up run)? Reuse it.
    if branch_exists(ref.repo_root, ref.branch):
        _run(["git", "worktree", "add", str(ref.path), ref.branch], cwd=ref.repo_root)
        note = f"reused existing branch {ref.branch}"
    else:
        _run(["git", "worktree", "add", str(ref.path), "-b", ref.branch, base_ref], cwd=ref.repo_root)
        note = f"created branch {ref.branch} off {base_ref}"

    return {
        "path": str(ref.path),
        "branch": ref.branch,
        "created": True,
        "note": note,
    }


def cleanup_worktree(ref: WorktreeRef, delete_branch: bool = False, force: bool = False) -> dict:
    """Remove the worktree (and optionally its branch)."""
    found = False
    for wt in existing_worktrees(ref.repo_root):
        if _resolve(wt.get("worktree", "")) == ref.path:
            found = True
            break

    if not found:
        return {"path": str(ref.path), "removed": False, "note": "worktree not registered"}

    args = ["git", "worktree", "remove"]
    if force:
        args.append("--force")
    args.append(str(ref.path))
    _run(args, cwd=ref.repo_root)

    result = {"path": str(ref.path), "removed": True, "branch_deleted": False, "note": "worktree removed"}

    if delete_branch and branch_exists(ref.repo_root, ref.branch):
        try:
            _run(["git", "branch", "-D", ref.branch], cwd=ref.repo_root)
            result["branch_deleted"] = True
            result["note"] = f"worktree removed and branch {ref.branch} deleted"
        except subprocess.CalledProcessError as e:
            result["note"] = f"worktree removed but branch delete failed: {e.stderr.strip()}"

    return result


def info_worktree(ref: WorktreeRef) -> dict:
    for wt in existing_worktrees(ref.repo_root):
        if _resolve(wt.get("worktree", "")) == ref.path:
            return {
                "path": str(ref.path),
                "branch": ref.branch,
                "head": wt.get("HEAD", ""),
                "exists": True,
            }
    return {"path": str(ref.path), "branch": ref.branch, "exists": False}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--workdir", default=".",
        help="path inside the target repo (default: cwd). Used to locate the repo root.",
    )
    p.add_argument(
        "--target", required=True,
        help="optimization target (e.g. 'build-time', 'latency and cost'). "
             "Used to derive the worktree slug and branch name.",
    )
    p.add_argument(
        "--json", action="store_true",
        help="JSON output (default: human-readable single-line)",
    )

    sub = p.add_subparsers(dest="cmd", required=True)
    sub_init = sub.add_parser("init", help="create or reuse the multi-goal worktree")
    sub_init.add_argument(
        "--base", default="HEAD",
        help="ref to branch from when the worktree branch is new (default: HEAD)",
    )
    sub.add_parser("info", help="print resolved worktree path / branch / head")
    sub_cleanup = sub.add_parser("cleanup", help="remove the worktree after the run")
    sub_cleanup.add_argument(
        "--delete-branch", action="store_true",
        help="also delete the multigoal/<slug> branch",
    )
    sub_cleanup.add_argument(
        "--force", action="store_true",
        help="force-remove the worktree even if it has local changes",
    )

    args = p.parse_args(argv)

    try:
        repo_root = resolve_repo_root(Path(args.workdir).resolve())
    except RuntimeError as e:
        sys.stderr.write(f"{e}\n")
        return 2

    slug = slugify(args.target)
    ref = WorktreeRef(repo_root=repo_root, slug=slug)

    try:
        if args.cmd == "init":
            result = init_worktree(ref, base_ref=args.base)
        elif args.cmd == "info":
            result = info_worktree(ref)
            if not result.get("exists"):
                # Info on a non-existent worktree → exit 1 so callers can branch
                if args.json:
                    print(json.dumps(result, indent=2))
                else:
                    print(f"not initialized: {result['path']}")
                return 1
        elif args.cmd == "cleanup":
            result = cleanup_worktree(ref, delete_branch=args.delete_branch, force=args.force)
        else:
            raise RuntimeError(f"unknown subcommand: {args.cmd}")
    except (subprocess.CalledProcessError, RuntimeError) as e:
        msg = str(e) if isinstance(e, RuntimeError) else (e.stderr or str(e))
        sys.stderr.write(f"error: {msg}\n")
        return 2

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if args.cmd == "init":
            print(f"{result['path']}  ({result['note']})")
        elif args.cmd == "info":
            print(f"{result['path']}  branch={result['branch']}  head={result.get('head', '?')}")
        elif args.cmd == "cleanup":
            print(result["note"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
