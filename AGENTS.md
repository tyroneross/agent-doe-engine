<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# agent-doe-engine: cross-host guide

A design-of-experiments engine for tuning AI agents.

It runs a small, structured set of trials that change several settings at once, then measures which settings actually move each metric you care about (speed, cost, quality, accuracy) and which combination best balances goals that compete. It separates real effects from flukes, and flags when two settings are tangled so you can't credit one over the other. Built for agent tuning: which model, which prompt, which setup, answered in a few runs instead of guessing.

This file is the host-neutral version of the workflow for non-Claude coding agents (Codex, Copilot, Cursor, ...). Claude Code uses `skills/agent-doe-engine/SKILL.md`; the method is identical.

## Method

Optimize one or more measurable numbers by experiment, never by intuition.

1. **Setup.** Identify factors (things you can change: constants, env vars, config) and objectives (numbers you can measure with a one-line command). Each objective has a `direction` (lower/higher) and a `weight`. Pick a `selection` method (`scalarize` | `desirability` | `pareto`).
2. **Design.** With >=2 factors, generate a DOE matrix: `python3 scripts/doe.py detect <k>` then `python3 scripts/doe.py generate --factors <json> --design auto`. With 1 factor, use the autoresearch loop (`scripts/loop.py`).
3. **Run.** For each row in the design (randomized order): apply the factor values, measure every objective via its `metric_cmd`, run the guard, record `{run_id, values:{...}, guard_ok}` to `.agent-doe-engine/optimize/results.jsonl`, then revert (each DOE run starts from the same baseline).
4. **Analyze.** `python3 scripts/doe.py analyze --design doe.json --results results.jsonl --objectives objectives.json` gives ranked effects per objective plus the best run by the chosen selection method. Apply the winning combination as one commit.
5. **Review.** Check for overfitting / metric-gaming (the `overfitting-reviewer` rubric) before accepting.

## Multi-objective rules

- Single objective behaves exactly like a single-metric optimizer.
- `scalarize`: minimize/maximize the weighted sum of min-max-normalized responses.
- `desirability`: Derringer-Suich. Transform each response to di in [0,1], combine as D = (prod di^wi)^(1/sum w); pick max D.
- `pareto`: return the non-dominated set; if one winner is needed, the highest-desirability point on the front.

## Host adaptation

Hypothesis generation and factor confirmation are the host LLM's job. The scripts are deterministic and host-neutral. Spawn parallel workers only when the user explicitly authorizes delegation.
