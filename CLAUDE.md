# multi-goal

Multi-objective optimization via Design of Experiments. Optimize several competing metrics at once — find the factor settings that best trade off latency, cost, bundle size, coverage, etc.

## What it does

Two modes, both judged purely by measured numbers (no vibes):

- **DOE mode (default, multi-variable).** Plan a Design of Experiments matrix, run each combination, measure every objective on each run, fit effects per objective, and select the best run by one of three methods: weighted **scalarization**, Derringer-Suich **desirability**, or **Pareto** frontier. Tells you which factors actually move each number and which only co-varied. 2–3 factors → full factorial (4–8 runs); 4–7 → fractional factorial (8 runs); 8–11 → Plackett-Burman 12-run screening.
- **Autoresearch mode (fallback, single/few variable).** Greedy loop: hypothesize one atomic change, measure, keep if the aggregate objective score improves and guards pass, else revert. Cannot see interactions; cheaper to set up.

## Core conventions (binding for all contributors)

- **Runtime data path**: state lives under `.multi-goal/` in the *consumer* project, never `.build-loop/` or `.claude/`. The optimization workspace is `.multi-goal/optimize/` (`experiment.json`, `doe.json`, `results.jsonl`, `effects.json`, `objectives.json`, `experiments/` archive).
- **Dependencies**: numpy only for runtime; pytest for dev. No other packages. uv for all Python (`uv run pytest`, `uv run python scripts/...`).
- **Host-agnostic**: the host coding agent's LLM does the reasoning (hypothesis generation, factor confirmation). Scripts provide deterministic math + structured data; the skill instructs the host LLM. Same plugin works under Claude Code, Codex, etc. No direct vendor API calls.
- **Attribution**: every source file carries the SPDX header
  `# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>`
  `# SPDX-License-Identifier: Apache-2.0`
  Markdown skill/agent files use the HTML-comment form. JSON is covered by REUSE.toml.

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
- The autoresearch keep/revert rule uses the aggregate score (scalarize/desirability). Pareto is a DOE-analyze + reporting mode; in the loop it degrades to scalarize for the keep/revert decision (documented fallback).

## Layout

```
.claude-plugin/plugin.json   plugin manifest
skills/multi-goal/           SKILL.md (orchestration) + profiles.md
commands/multi-goal.md       slash command entry
agents/                      optimize-runner, overfitting-reviewer
scripts/objectives.py        multi-objective core: scalarize, desirability, pareto_front, normalize
scripts/doe.py               DOE matrix generation + multi-response effects analysis
scripts/loop.py              single/few-variable autoresearch loop
scripts/suggest_factors.py   codebase scanner for factor candidates
scripts/metric_runner.py     sampled metric/guard execution
tests/                       pytest suite
docs/                        method notes + usage
```
