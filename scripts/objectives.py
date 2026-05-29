# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""
Multi-objective core: scalarization, Derringer-Suich desirability, Pareto dominance.

Public API (contract — do not rename or reshape):
    compute_bounds, normalize, scalarize_run, desirability_run,
    dominates, pareto_front, select_best
"""

from __future__ import annotations

import math
from typing import Any


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _default_objective(obj: dict) -> dict:
    """Return objective dict with defaults applied (non-mutating)."""
    return {
        "name": obj["name"],
        "direction": obj.get("direction", "lower"),
        "weight": float(obj.get("weight", 1.0)),
    }


def _normalized_weights(objectives: list[dict]) -> list[float]:
    """Return per-objective weights normalized to sum to 1.0."""
    weights = [float(o.get("weight", 1.0)) for o in objectives]
    total = sum(weights)
    if total == 0.0:
        # Guard: all-zero weights → equal weighting
        n = len(weights)
        return [1.0 / n] * n
    return [w / total for w in weights]


def _is_at_least_as_good(a_val: float, b_val: float, direction: str) -> bool:
    """True if a_val is >= b_val in the context of `direction`."""
    if direction == "higher":
        return a_val >= b_val
    return a_val <= b_val  # "lower" is better


def _is_strictly_better(a_val: float, b_val: float, direction: str) -> bool:
    """True if a_val is strictly better than b_val for `direction`."""
    if direction == "higher":
        return a_val > b_val
    return a_val < b_val


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_bounds(runs: list[dict], objectives: list[dict]) -> dict:
    """
    runs = [{"run_id": int, "values": {obj_name: float, ...}}, ...].
    objectives = [{"name": str, "direction": "lower"|"higher", "weight": float}, ...].
    Returns {obj_name: {"min": float, "max": float}} computed across all runs.
    Raises ValueError if a run is missing an objective's value.
    """
    obj_names = [o["name"] for o in objectives]
    bounds: dict[str, dict[str, float]] = {}

    for name in obj_names:
        values: list[float] = []
        for run in runs:
            run_vals = run.get("values", {})
            if name not in run_vals:
                raise ValueError(
                    f"Run {run.get('run_id', '?')} missing value for objective '{name}'"
                )
            values.append(float(run_vals[name]))
        bounds[name] = {"min": min(values), "max": max(values)}

    return bounds


def normalize(value: float, direction: str, lo: float, hi: float) -> float:
    """
    Min-max normalize to [0,1] where 1.0 == best.
    direction 'higher': (value-lo)/(hi-lo).
    direction 'lower':  (hi-value)/(hi-lo).
    Degenerate hi==lo -> return 1.0.
    """
    if hi == lo:
        return 1.0
    if direction == "higher":
        return (value - lo) / (hi - lo)
    # direction == "lower"
    return (hi - value) / (hi - lo)


def scalarize_run(values: dict, objectives: list[dict], bounds: dict) -> float:
    """
    Weighted sum of normalized responses. Weights normalized internally to sum to 1.
    Higher return = better. Range [0,1].
    """
    w_norm = _normalized_weights(objectives)
    score = 0.0
    for w, obj in zip(w_norm, objectives):
        name = obj["name"]
        direction = obj.get("direction", "lower")
        lo = bounds[name]["min"]
        hi = bounds[name]["max"]
        d = normalize(float(values[name]), direction, lo, hi)
        score += w * d
    return score


def desirability_run(values: dict, objectives: list[dict], bounds: dict) -> float:
    """
    Derringer-Suich overall desirability D.
    Per objective: d_i = normalize(value, direction, lo, hi)  (one-sided, linear).
    D = (prod_i d_i ** w_i) ** (1 / sum_i w_i).
    If any d_i == 0 -> D == 0 (a hard fail on one objective tanks the run).
    Range [0,1].
    """
    weights = [float(obj.get("weight", 1.0)) for obj in objectives]
    w_sum = sum(weights)
    if w_sum == 0.0:
        w_sum = float(len(weights))
        weights = [1.0] * len(weights)

    log_sum = 0.0
    for w, obj in zip(weights, objectives):
        name = obj["name"]
        direction = obj.get("direction", "lower")
        lo = bounds[name]["min"]
        hi = bounds[name]["max"]
        d_i = normalize(float(values[name]), direction, lo, hi)
        if d_i == 0.0:
            return 0.0
        log_sum += w * math.log(d_i)

    return math.exp(log_sum / w_sum)


def dominates(a_values: dict, b_values: dict, objectives: list[dict]) -> bool:
    """
    True if run A Pareto-dominates run B: A is at-least-as-good on EVERY objective
    (respecting each objective's direction) AND strictly better on at least one.
    """
    at_least_as_good_all = True
    strictly_better_one = False

    for obj in objectives:
        name = obj["name"]
        direction = obj.get("direction", "lower")
        a_val = float(a_values[name])
        b_val = float(b_values[name])

        if not _is_at_least_as_good(a_val, b_val, direction):
            at_least_as_good_all = False
            break
        if _is_strictly_better(a_val, b_val, direction):
            strictly_better_one = True

    return at_least_as_good_all and strictly_better_one


def pareto_front(runs: list[dict], objectives: list[dict]) -> list[int]:
    """Return sorted list of run_id values that are non-dominated."""
    non_dominated: list[int] = []

    for i, run_a in enumerate(runs):
        dominated = False
        for j, run_b in enumerate(runs):
            if i == j:
                continue
            if dominates(run_b["values"], run_a["values"], objectives):
                dominated = True
                break
        if not dominated:
            non_dominated.append(run_a["run_id"])

    return sorted(non_dominated)


def select_best(
    runs: list[dict],
    objectives: list[dict],
    method: str = "scalarize",
) -> dict:
    """
    method in {"scalarize","desirability","pareto"}.

    Returns:
    {
      "method": str,
      "bounds": {obj_name: {"min":..,"max":..}},
      "scores": [{"run_id": int, "score": float}, ...]   # score per run by the method
                                                          # (for pareto: desirability),
      "best_run_id": int,        # argmax score; for pareto, max-desirability run within the front
      "best_score": float,
      "pareto_front": [run_id, ...],   # always computed and returned regardless of method
      "best_values": {obj_name: float} # raw measured values of the best run
    }

    Single-objective degenerate case (len(objectives)==1): best_run_id is the run with the
    best raw value for that objective's direction; scores still populated;
    pareto_front == [that run].
    """
    valid_methods = {"scalarize", "desirability", "pareto"}
    if method not in valid_methods:
        raise ValueError(
            f"Unknown method '{method}'. Must be one of: {sorted(valid_methods)}"
        )

    bounds = compute_bounds(runs, objectives)
    front = pareto_front(runs, objectives)

    # --- compute per-run scores -------------------------------------------
    if method == "scalarize":
        score_fn = lambda run: scalarize_run(run["values"], objectives, bounds)
    else:
        # desirability for both "desirability" and "pareto" methods
        score_fn = lambda run: desirability_run(run["values"], objectives, bounds)

    scores = [
        {"run_id": run["run_id"], "score": score_fn(run)}
        for run in runs
    ]

    # --- pick best run -------------------------------------------------------
    if method == "pareto":
        # best is the max-desirability run within the Pareto front
        front_set = set(front)
        candidate_scores = [s for s in scores if s["run_id"] in front_set]
        if not candidate_scores:
            # Shouldn't happen (at least one run is non-dominated), fall back
            candidate_scores = scores
    else:
        candidate_scores = scores

    best_entry = max(candidate_scores, key=lambda s: s["score"])
    best_run_id = best_entry["run_id"]
    best_score = best_entry["score"]

    # look up raw values for best run
    best_run = next(r for r in runs if r["run_id"] == best_run_id)
    best_values = {name: float(best_run["values"][name]) for name in best_run["values"]}

    return {
        "method": method,
        "bounds": bounds,
        "scores": scores,
        "best_run_id": best_run_id,
        "best_score": best_score,
        "pareto_front": front,
        "best_values": best_values,
    }


# ---------------------------------------------------------------------------
# Streaming-loop scoring (autoresearch / loop.py)
# ---------------------------------------------------------------------------

# NOTE: baseline_aggregate uses a different normalization strategy than
# select_best's batch min-max.  select_best sees ALL runs simultaneously and
# can normalize each objective to [0,1] across the observed range.  The
# autoresearch loop is *streaming* — it scores one candidate at a time against
# a fixed starting point.  Using batch min-max here would require re-running
# every past candidate on every iteration.  Instead we express each
# objective's score as an improvement RATIO vs the fixed baseline: a ratio > 1
# means the candidate beats the baseline on that objective.  The weighted sum
# of ratios gives a scalar that the loop can compare across streaming
# iterations without ever needing to collect a run set first.

_BASELINE_EPSILON = 1e-9  # guard against division by zero (documented below)


def baseline_aggregate(values: dict, baseline: dict, objectives: list[dict]) -> float:
    """Weighted aggregate of per-objective improvement RATIOS vs a fixed baseline.

    For each objective (weights normalized to sum to 1):
      'lower'  better: r_i = baseline_i / value_i   (>1 = improved)
      'higher' better: r_i = value_i / baseline_i   (>1 = improved)

    Returns sum_i w_i * r_i. Higher = better. Guards against div-by-zero:
    when the denominator is 0 (or smaller than _BASELINE_EPSILON) it is
    replaced with _BASELINE_EPSILON (1e-9).  This makes the ratio very large
    when the baseline was zero and the new value is positive (big improvement)
    or 0 when the new value is also zero (no change).  The epsilon value is
    documented here and in tests so callers know the guard is active.

    This is the loop's keep/revert score; it is distinct from select_best's
    batch min-max normalization because the loop is streaming (one candidate
    at a time against a fixed starting point).
    """
    w_norm = _normalized_weights(objectives)
    total = 0.0
    for w, obj in zip(w_norm, objectives):
        name = obj["name"]
        direction = obj.get("direction", "lower")
        v = float(values[name])
        b = float(baseline[name])

        if direction == "lower":
            # lower is better: ratio > 1 when value < baseline
            denom = v if v >= _BASELINE_EPSILON else _BASELINE_EPSILON
            r_i = b / denom
        else:
            # higher is better: ratio > 1 when value > baseline
            denom = b if b >= _BASELINE_EPSILON else _BASELINE_EPSILON
            r_i = v / denom

        total += w * r_i
    return total
