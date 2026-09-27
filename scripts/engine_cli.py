# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""One vendor-neutral entry point for the existing DOE command-line tools."""
import argparse
import runpy
import sys
from pathlib import Path


COMMANDS = {
    "doe": "doe",
    "analyst": "analyst",
    "paired": "paired_analysis",
    "ledger": "experiment_ledger",
    "report": "report",
    "assess": "semantic_assessor",
    "loop": "loop",
    "metric": "metric_runner",
    "suggest-factors": "suggest_factors",
    "validate-factors": "validate_factors",
    "worktree": "worktree",
}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=COMMANDS)
    parser.add_argument("args", nargs=argparse.REMAINDER,
                        help="Arguments for the selected tool; use COMMAND --help")
    args = parser.parse_args(argv)
    previous = sys.argv
    previous_path = sys.path[:]
    script_dir = Path(__file__).resolve().parent
    try:
        sys.argv = [f"agent-doe-engine {args.command}", *args.args]
        sys.path.insert(0, str(script_dir))
        runpy.run_path(str(script_dir / (COMMANDS[args.command] + ".py")), run_name="__main__")
    finally:
        sys.argv = previous
        sys.path[:] = previous_path
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
