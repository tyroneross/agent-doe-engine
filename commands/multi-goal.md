---
description: Multi-objective optimization via Design of Experiments — optimize competing metrics (latency + cost + size) at once and find the best trade-off. Falls back to a single-variable autoresearch loop.
argument-hint: "[target or 'latency and cost' or factor list]"
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

Run the multi-goal optimizer for: $ARGUMENTS

Invoke the `multi-goal` skill and follow its three phases:

1. **Setup** — name the objectives (each with direction + weight + a one-line metric command), pick a selection method (`scalarize` / `desirability` / `pareto`), and identify the factors to test. If the user did not name factors, run `scripts/suggest_factors.py` and confirm candidates before proceeding.
2. **Run** — generate the DOE matrix (≥2 factors) or initialize the autoresearch loop (1 factor), measure every objective on every run, and analyze with `scripts/doe.py analyze --objectives ...`.
3. **Review** — dispatch `overfitting-reviewer`, summarize per-objective improvement and the chosen trade-off, archive.

If $ARGUMENTS names a single number and a single knob, go straight to the autoresearch loop. If it names competing goals ("faster and cheaper"), set up a multi-objective `objectives.json`. Never auto-run optimization on heuristic factor candidates without explicit user confirmation.
