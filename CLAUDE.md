<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# agent-doe-engine

A design-of-experiments engine for tuning AI agents.

It runs a small, structured set of trials that change several settings at once, then measures which settings actually move each metric you care about (speed, cost, quality, accuracy) and which combination best balances goals that compete. It separates real effects from flukes, and flags when two settings are tangled so you can't credit one over the other. Built for agent tuning: which model, which prompt, which setup, answered in a few runs instead of guessing.

## What it does

Two modes, both judged purely by measured numbers (no vibes):

- **DOE mode (default, multi-variable).** Plan a Design of Experiments matrix, run each combination, measure every objective on each run, fit effects per objective, and select the best run by one of three methods: weighted **scalarization**, Derringer-Suich **desirability**, or **Pareto** frontier. Tells you which factors actually move each number and which only co-varied. 2 to 3 factors give a full factorial (4 to 8 runs); 4 to 7 give a fractional factorial (8 runs); 8 to 11 give a Plackett-Burman 12-run screening.
- **Autoresearch mode (fallback, single/few variable).** Greedy loop: hypothesize one atomic change, measure, keep if the aggregate objective score improves and guards pass, else revert. Cannot see interactions; cheaper to set up.

## Core conventions (binding for all contributors)

- **Runtime data path**: state lives under `.agent-doe-engine/` in the *consumer* project, never `.build-loop/` or `.claude/`. The optimization workspace is `.agent-doe-engine/optimize/` (`experiment.json`, `doe.json`, `results.jsonl`, `effects.json`, `objectives.json`, `experiments/` archive). A legacy `.multi-goal/` dir from before the rename is migrated into place automatically on first use.
- **Dependencies**: numpy only for runtime; pytest for dev. No other packages. uv for all Python (`uv run pytest`, `uv run python scripts/...`).
- **Host-agnostic**: the host coding agent's LLM does the reasoning (hypothesis generation, factor confirmation). Scripts provide deterministic math plus structured data; the skill instructs the host LLM. Same plugin works under Claude Code, Codex, etc. No direct vendor API calls.
- **Attribution**: every source file carries a two-line SPDX header: a `FileCopyrightText` tag (`2025-2026 Tyrone Ross, Jr` plus the GitHub noreply email) and an `Apache-2.0` license tag, copied from any existing `scripts/*.py`. Python uses `#` comments; markdown uses the HTML-comment form (two separate comment lines, not one combined line, because REUSE parses them per line). JSON, `.gitignore`, and `LICENSE` are covered by `REUSE.toml`. Validate with `uvx reuse lint`.

## Multi-objective contract (the new capability vs. the build-loop original)

Objectives are a list. Each objective names a metric command, a direction, and a weight:

```json
{
  "objectives": [
    {"name": "latency_ms",   "direction": "lower",  "weight": 0.5, "metric_cmd": "..."},
    {"name": "cost_usd",     "direction": "lower",  "weight": 0.3, "metric_cmd": "..."},
    {"name": "coverage_pct", "direction": "higher", "weight": 0.2, "metric_cmd": "..."}
  ],
  "selection": "scalarize"
}
```

- `selection`: `scalarize` (weighted sum of min-max-normalized responses) | `desirability` (Derringer-Suich D = weighted geometric mean of per-response desirabilities) | `pareto` (report the non-dominated set; for a single winner, max desirability within the front).
- A single objective (`len(objectives) == 1`) is the degenerate case and must behave exactly like the original single-metric optimizer.
- DOE results carry every response per run: `{"run_id": i, "values": {"latency_ms": .., "cost_usd": ..}, "guard_ok": true}`. The analyzer fits OLS effects **per objective** and computes the aggregate score per run for selection.
- The autoresearch keep/revert rule uses the aggregate score (scalarize/desirability). Pareto is a DOE-analyze plus reporting mode; in the loop it degrades to scalarize for the keep/revert decision (documented fallback).

## Layout

```
.claude-plugin/plugin.json   plugin manifest
skills/agent-doe-engine/     SKILL.md (orchestration) + profiles.md
commands/agent-doe.md        slash command entry
agents/                      optimize-runner, overfitting-reviewer
scripts/objectives.py        multi-objective core: scalarize, desirability, pareto_front, normalize
scripts/doe.py               DOE matrix generation + multi-response effects analysis
scripts/loop.py              single/few-variable autoresearch loop
scripts/suggest_factors.py   codebase scanner for factor candidates
scripts/metric_runner.py     sampled metric/guard execution
tests/                       pytest suite
docs/                        method notes + usage
```
