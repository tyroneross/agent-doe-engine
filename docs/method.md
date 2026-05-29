<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Method

multi-goal turns "make it better" into a measured experiment. Two engines.

## 1. Design of Experiments (multi-variable)

Instead of changing one knob at a time, run a structured matrix that varies several factors together, then attribute the change in each objective to each factor.

| Factors `k` | Design | Runs | Resolves |
|---|---|---|---|
| 2–3 | 2^k full factorial | 4–8 | all main effects + interactions |
| 4–7 | 2^(k-p) fractional factorial (Res III/IV) | 8 | main effects (some aliased with interactions) |
| 8–11 | Plackett-Burman | 12 | main-effects screening only |

Each factor is coded ±1 (low/high). The analyzer fits, per objective, an ordinary-least-squares model `y ~ intercept + main effects + 2-way interactions` (interactions only when degrees of freedom allow) and ranks factors by absolute effect size. A factor with a large effect genuinely moves the number; one that only co-varied shows a small effect.

The DOE matrix math (full factorial enumeration, fractional generators, the 12-run Plackett-Burman Paley construction) is numpy-only and was verified equivalent to pyDOE3 up to row/column permutation.

## 2. Autoresearch (single/few-variable greedy)

When there's one thing to try, loop: hypothesize one atomic change → measure → keep if better, revert if not. Cheaper to set up, blind to interactions.

## Multi-objective selection

When ≥2 objectives compete, "best" needs a definition. multi-goal offers three, all in `scripts/objectives.py`.

### scalarize (default)

Each objective is min-max normalized across the runs so `1.0` = best observed, `0.0` = worst, respecting direction (for `lower`, smaller raw → higher normalized). The run score is the weighted sum `Σ wᵢ · normᵢ` (weights normalized to sum 1). Pick the max. Simple, interpretable, assumes you can express priorities as weights.

### desirability (Derringer-Suich)

Each objective maps to a desirability `dᵢ ∈ [0,1]` (here the same one-sided linear normalization). The overall desirability is the weighted geometric mean:

```
D = (∏ᵢ dᵢ^wᵢ) ^ (1 / Σ wᵢ)
```

The geometric mean means a `dᵢ = 0` on any single objective forces `D = 0` — a run that fails one goal cannot be rescued by excelling at the others. Use when every objective must clear a bar. (Derringer & Suich, *Journal of Quality Technology*, 1980 — the standard multi-response DOE technique.)

### pareto

Return the non-dominated set: a run is on the front if no other run is at-least-as-good on every objective and strictly better on at least one. The front is the set of rational trade-offs; nothing off it should ever be chosen. When a single winner is required, multi-goal picks the highest-desirability point on the front. The front is **always** computed and returned regardless of the selection method, so you can inspect trade-offs even when you asked for a scalar pick.

## Why the loop scores differently from DOE

DOE normalizes across a fixed batch of runs (it has all of them). The greedy loop is streaming — there is no fixed set to min-max against. So `loop.py` scores each candidate as the weighted sum of per-objective *improvement ratios* versus a fixed baseline measured at init (`baseline_aggregate`): for `lower`, `baseline/value`; for `higher`, `value/baseline`; `> 1` means net improvement. This keeps keep/revert decisions stable across iterations. Both live in `objectives.py` so the math has one home.
