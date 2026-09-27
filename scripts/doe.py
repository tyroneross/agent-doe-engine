#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""DOE matrix generation + effects analysis for agent-doe-engine:optimize.

Stdlib + numpy only. Provably equivalent to pyDOE3 1.6.2 for the three
designs we care about (full factorial, fractional factorial, Plackett-Burman
12-run) - verified by side-by-side comparison with off-diag(XᵀX)=0 and
matching matrices up to row/column permutation.

The `analyze` subcommand now supports multiple objectives. Pass --objectives
to supply a list of {name, direction, weight} specs and receive per-objective
ranked effects plus a unified selection result from objectives.select_best.

Subcommands:
  generate --factors <json> [--design auto|full|fractional|pb] [--seed N]
      Print a JSON design matrix + run order. Each row is one experimental
      condition with named factor values (mapped from ±1 coding to the user-
      specified levels).

  analyze --design <json> --results <jsonl>
          [--objectives <json-or-path>] [--selection scalarize|desirability|pareto]
      Read measured responses, fit OLS effects (intercept + main + 2-way),
      print ranked findings as JSON. With --objectives, performs multi-objective
      analysis and calls objectives.select_best for unified run selection.

  detect <factor-count>
      Print which design type would be auto-selected for k factors.

Design routing:
  k == 1   → autoresearch (recommended; this script returns an error)
  2 ≤ k ≤ 3 → full factorial 2^k    (≤8 runs)
  4 ≤ k ≤ 7 → fractional factorial 2^(k-p) Resolution III/IV (8 runs)
  k ≥ 8    → Plackett-Burman 12-run screening (handles up to 11)
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
from pathlib import Path

# Robust import of objectives.py regardless of CWD
sys.path.insert(0, str(Path(__file__).resolve().parent))
import objectives  # noqa: E402
import doe_stats  # noqa: E402

try:
    import numpy as np
except ImportError:  # pragma: no cover
    sys.stderr.write(
        "doe.py requires numpy. Install with: pip install numpy\n"
    )
    sys.exit(2)


# ---------------------------------------------------------------------------
# Design generators (mirrors pyDOE3 - see tests/test_doe.py)
# ---------------------------------------------------------------------------

def full_factorial_2level(k: int) -> np.ndarray:
    """2^k full factorial with each column at ±1."""
    return np.array(list(itertools.product([-1, 1], repeat=k)), dtype=float)


# Standard Resolution III/IV generator strings for k factors at 8 runs.
# Sources: Montgomery, Design and Analysis of Experiments, Table 8.14;
# matched to pyDOE3.fracfact() output for k=4..7.
FRACFACT_8_RUN = {
    4: "a b c abc",                       # 2^(4-1) Resolution IV
    5: "a b c ab ac",                     # 2^(5-2) Resolution III
    6: "a b c ab ac bc",                  # 2^(6-3) Resolution III
    7: "a b ab c ac bc abc",              # 2^(7-4) Resolution III (saturated)
}


def fracfact(generators: str) -> np.ndarray:
    """2-level fractional factorial via generator string. Each token is the
    elementwise product of its base-letter columns from the underlying full
    factorial over the unique base letters."""
    tokens = generators.split()
    base_letters = sorted({c for tok in tokens for c in tok if c.isalpha()})
    base_design = full_factorial_2level(len(base_letters))
    letter_to_col = {l: base_design[:, i] for i, l in enumerate(base_letters)}
    cols = []
    for tok in tokens:
        col = np.ones(base_design.shape[0])
        for c in tok:
            if c.isalpha():
                col = col * letter_to_col[c]
        cols.append(col)
    return np.column_stack(cols)


def plackett_burman_12() -> np.ndarray:
    """12-run Plackett-Burman (Paley construction, cyclic generator).
    Handles up to 11 factors; orthogonal main-effects screening only."""
    gen = np.array([+1, +1, -1, +1, +1, +1, -1, -1, -1, +1, -1])
    rows = [gen.copy()]
    for _ in range(10):
        gen = np.roll(gen, -1)
        rows.append(gen.copy())
    rows.append(np.full(11, -1))
    return np.array(rows, dtype=float)


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

def select_design(k: int) -> str:
    if k <= 0:
        raise ValueError("factor count must be ≥1")
    if k == 1:
        return "autoresearch"  # caller should fall back to single-var loop
    if k <= 3:
        return "full"
    if k <= 7:
        return "fractional"
    return "pb"


def build_design(k: int, design_type: str) -> tuple[np.ndarray, str]:
    """Return (matrix, name). Matrix has shape (n_runs, k)."""
    if design_type == "full":
        return full_factorial_2level(k), f"2^{k} full factorial"
    if design_type == "fractional":
        if k not in FRACFACT_8_RUN:
            raise ValueError(
                f"no curated 8-run fractional design for k={k}; supported: {sorted(FRACFACT_8_RUN)}"
            )
        return fracfact(FRACFACT_8_RUN[k]), f"2^({k}-{k-3}) fractional factorial"
    if design_type == "pb":
        if k > 11:
            raise ValueError(f"PB-12 supports up to 11 factors; got {k}")
        full = plackett_burman_12()
        return full[:, :k], f"Plackett-Burman 12-run (using {k} of 11 factors)"
    raise ValueError(f"unknown design type: {design_type}")


# ---------------------------------------------------------------------------
# Alias / confounding structure
# ---------------------------------------------------------------------------

def _term_label(term: tuple[int, ...], factor_names: list[str] | None) -> str:
    """Human label for an effect term given as a tuple of factor indices.
    () → 'I' (the identity / grand mean column)."""
    if not term:
        return "I"
    if factor_names is not None:
        return "·".join(factor_names[i] for i in term)
    # Letter coding A, B, C, … when no names supplied.
    return "".join(chr(ord("A") + i) for i in term)


def alias_structure(design: np.ndarray, factor_names: list[str] | None = None,
                    max_order: int = 2) -> dict:
    """Compute the confounding structure of a 2-level design empirically.

    Two effects are aliased iff their ±1 model columns are identical up to
    sign - that is the operational definition of confounding, independent of
    how the design was generated. We enumerate the grand mean, all main
    effects, and all interactions up to `max_order`, group terms whose columns
    coincide, and read the resolution off the shortest defining-relation word.

    Returns:
      {
        "resolution": "III" | "IV" | "V" | "Full" | "None",
        "resolution_int": int | None,
        "defining_relation": ["I = ABD = ACE = BCDE", ...] as a list of words,
        "alias_chains": [["A", "BD", ...], ...],   # each chain = confounded set
        "aliasing": bool,
        "note": str,
      }

    For a full factorial (no two enumerated effects share a column) the result
    states "no aliasing (full factorial)".
    """
    n, k = design.shape

    def column(term: tuple[int, ...]) -> np.ndarray:
        col = np.ones(n)
        for idx in term:
            col = col * design[:, idx]
        return col

    def canon_key(col: np.ndarray) -> tuple:
        """Canonical key for a column up to sign (first nonzero entry → +)."""
        sign = 1.0
        for v in col:
            if v != 0:
                sign = 1.0 if v > 0 else -1.0
                break
        return tuple(np.round(col * sign, 9))

    identity_col = tuple(np.round(np.ones(n), 9))

    # --- Defining relation: search ALL orders for words equal to the I column.
    # A regular fractional design's identity words can be any length up to k,
    # so we must enumerate every subset to recover the full generating set.
    defining_words: list[tuple[int, ...]] = []
    for order in range(1, k + 1):
        for combo in itertools.combinations(range(k), order):
            if canon_key(column(combo)) == identity_col:
                defining_words.append(combo)
    defining_words.sort(key=lambda t: (len(t), t))

    # --- Alias chains among the readable effects (mains + ≤max_order inter).
    readable_terms: list[tuple[int, ...]] = []
    for order in range(1, min(max_order, k) + 1):
        readable_terms.extend(itertools.combinations(range(k), order))

    groups: dict[tuple, list[tuple[int, ...]]] = {}
    for term in readable_terms:
        groups.setdefault(canon_key(column(term)), []).append(term)

    alias_chains: list[list[str]] = []
    for canon, members in groups.items():
        if canon == identity_col:
            continue
        if len(members) > 1:
            chain = sorted(members, key=lambda t: (len(t), t))
            alias_chains.append([_term_label(t, factor_names) for t in chain])
    alias_chains.sort(key=lambda c: (len(c[0]) if c else 0, c))

    # A design is non-regular (e.g. Plackett-Burman) when its columns are
    # orthogonal yet no exact ±1 alias group / identity word exists - its
    # confounding is fractional (partial), not full. Detect via off-diagonal
    # correlation between a main effect and any 2-way interaction column.
    partial_alias = False
    if not defining_words and not alias_chains and k >= 3:
        main_cols = [design[:, i] for i in range(k)]
        for i, j in itertools.combinations(range(k), 2):
            inter = design[:, i] * design[:, j]
            for m, mc in enumerate(main_cols):
                if m in (i, j):
                    continue
                if abs(float(mc @ inter)) > 1e-9:
                    partial_alias = True
                    break
            if partial_alias:
                break

    aliasing = bool(defining_words) or bool(alias_chains) or partial_alias

    if not aliasing:
        return {
            "resolution": "Full",
            "resolution_int": None,
            "defining_relation": ["I"],
            "alias_chains": [],
            "aliasing": False,
            "note": "no aliasing (full factorial)",
        }

    if partial_alias and not defining_words and not alias_chains:
        return {
            "resolution": "III*",
            "resolution_int": 3,
            "defining_relation": ["I (non-regular - no clean defining relation)"],
            "alias_chains": [],
            "aliasing": True,
            "note": (
                "Non-regular design (Plackett-Burman): orthogonal main effects, "
                "but each main is PARTIALLY aliased with many two-way "
                "interactions. Use for main-effects screening only; do not "
                "interpret interactions."
            ),
        }

    # Resolution = length of the shortest defining-relation word.
    roman = {3: "III", 4: "IV", 5: "V", 6: "VI", 7: "VII"}
    if defining_words:
        res_int = min(len(w) for w in defining_words)
        resolution = roman.get(res_int, str(res_int))
        relation = "I = " + " = ".join(
            _term_label(w, factor_names) for w in defining_words
        )
    else:
        # Alias chains exist but no exact identity word recovered: report the
        # confounding without claiming a resolution number.
        res_int = None
        resolution = "Aliased"
        relation = "I"

    if res_int == 3:
        note = ("Resolution III: main effects are confounded with two-way "
                "interactions; interpret each aliased chain together, not as "
                "an isolated main effect.")
    elif res_int == 4:
        note = ("Resolution IV: main effects are clear of two-way interactions, "
                "but two-way interactions are confounded with each other.")
    elif res_int is not None and res_int >= 5:
        note = (f"Resolution {resolution}: main effects and two-way "
                "interactions are clear of each other.")
    else:
        note = ("Effects are aliased; see alias_chains. No clean defining "
                "relation recovered - interpret confounded terms together.")

    return {
        "resolution": resolution,
        "resolution_int": res_int,
        "defining_relation": [relation],
        "alias_chains": alias_chains,
        "aliasing": True,
        "note": note,
    }


# ---------------------------------------------------------------------------
# Effects analyzer
# ---------------------------------------------------------------------------

def _design_matrix(design: np.ndarray, include_interactions: bool
                   ) -> tuple[np.ndarray, list]:
    """Build the OLS design matrix X and its term labels.

    labels[0] == "intercept"; the rest are ("main", i) or ("inter", (i, j)).
    Truncates to a solvable column count when the model is over-parameterized.
    """
    n, k = design.shape
    cols = [np.ones(n)]
    labels: list = ["intercept"]
    for i in range(k):
        cols.append(design[:, i])
        labels.append(("main", i))
    if include_interactions:
        for i in range(k):
            for j in range(i + 1, k):
                cols.append(design[:, i] * design[:, j])
                labels.append(("inter", (i, j)))
    X = np.column_stack(cols)
    if X.shape[1] > X.shape[0]:
        X = X[:, : X.shape[0]]
        labels = labels[: X.shape[0]]
    return X, labels


# A residual standard deviation below this fraction of the response's own
# scale is treated as floating-point zero rather than as a measured error.
DEGENERATE_RESIDUAL_REL_TOL = 1e-9


def _inference_verdict(error_df: int) -> tuple[str, list[str]]:
    """Map error degrees of freedom → a plain-language trust verdict + warnings.

    The verdict tells consumers whether the p-values are estimates they can
    trust, directional-only, or absent (exact fit). It is the headline trust
    signal that stops over-reading effect magnitude on a saturated design.
    """
    warnings: list[str] = []
    if error_df <= 0:
        verdict = "saturated - no error df; effects are exact fits, not estimates"
        warnings.append(
            "Saturated design: 0 error degrees of freedom. Standard errors, "
            "t-statistics, and p-values cannot be computed. Effect magnitudes "
            "are exact fits to the data, not statistical estimates - add "
            "replicates or drop terms to obtain an error estimate."
        )
    elif error_df <= 3:
        verdict = f"low power - only {error_df} error df; directional only"
        warnings.append(
            f"Low power: only {error_df} error degrees of freedom. p-values are "
            "unstable; treat significance as directional, not conclusive. Add "
            "replicates to strengthen the error estimate."
        )
    else:
        verdict = "ok"
    return verdict, warnings


def fit_effects(design: np.ndarray, y: np.ndarray, include_interactions: bool = True,
                cell_values: list[list[float]] | None = None) -> dict:
    """Fit y ~ intercept + main + (optional) 2-way interactions via OLS, with
    per-effect statistical inference.

    Args:
      design: ±1 coded design matrix, shape (n_cells, k).
      y:      per-cell response (the cell MEAN when replicated), length n_cells.
      cell_values: optional list aligned to design rows; cell_values[i] is the
        list of replicate measurements at cell i. When any cell has ≥2
        replicates the error term is the pooled within-cell "pure error"
        (the statistically correct denominator for a stochastic response).
        When None or no cell is replicated, the error term is the OLS residual.

    Returns a dict carrying per-effect statistics (SE, t, p, 95% CI) keyed the
    same way as the legacy `main`/`interactions` point estimates, plus
    `residual_df`, `pure_error_df`, `error_var`, `inference`, and `warnings`.
    Backward compatible: callers passing only (design, y) keep working and now
    additionally receive inference based on the residual error term.
    """
    n, k = design.shape
    X, labels = _design_matrix(design, include_interactions)
    p_terms = X.shape[1]

    # Pure-error term (independent of the model) when cells are replicated.
    pure_error_var, pure_error_df = (0.0, 0)
    replicate_counts: list[int] | None = None
    if cell_values is not None:
        pure_error_var, pure_error_df = doe_stats.pooled_pure_error(cell_values)
        replicate_counts = [len(c) for c in cell_values]

    # When replicated, fit at OBSERVATION level so the point estimates and the
    # (XᵀX)⁻¹ used for standard errors come from one coherent OLS. For balanced
    # / orthogonal designs this is identical to the cell-mean fit; for
    # UNBALANCED replication it is the correct weighting (more replicates →
    # more influence). Unreplicated input fits the cell-level matrix as before.
    if pure_error_df > 0 and replicate_counts is not None:
        X_fit = np.repeat(X, replicate_counts, axis=0)
        y_fit = np.concatenate([np.asarray(c, dtype=float) for c in cell_values])
    else:
        X_fit, y_fit = X, y

    beta, _resid, rank, _ = np.linalg.lstsq(X_fit, y_fit, rcond=None)
    rank = int(rank)

    intercept = float(beta[0])
    main_effects = {labels[i][1]: float(beta[i]) for i in range(1, len(labels))
                    if labels[i][0] == "main"}
    inter_effects = {labels[i][1]: float(beta[i]) for i in range(1, len(labels))
                     if labels[i][0] == "inter"}

    # ---- Error term selection -------------------------------------------
    # Residual from the (observation-level when replicated) model fit.
    fitted = X_fit @ beta
    residual_ss = float(np.sum((y_fit - fitted) ** 2))
    residual_df = int(X_fit.shape[0] - rank)

    if pure_error_df > 0:
        # Replicated design: pooled within-cell pure error is the correct,
        # model-independent denominator for a stochastic response.
        error_var = pure_error_var
        error_df = pure_error_df
        error_source = "pure_error"
    elif residual_df > 0:
        error_var = residual_ss / residual_df
        error_df = residual_df
        error_source = "residual"
    else:
        # Saturated: no error df at all.
        error_var = 0.0
        error_df = 0
        error_source = "none"

    # ---- Variance explained (r²), reported at the observation level ------
    y_var = float(np.sum((y_fit - y_fit.mean()) ** 2))
    if y_var <= 0:
        r2 = 1.0
    elif rank == p_terms:
        r2 = 1.0 - residual_ss / y_var
    else:
        r2 = None

    # ---- Degenerate-fit detection ---------------------------------------
    # A residual at machine epsilon is not an error estimate, it is numerical
    # noise from a model that reproduces the response exactly. Testing against
    # it yields standard errors around 1e-16 and p-values around 1e-31, which
    # read as overwhelming evidence and are an artifact of dividing by zero.
    # Compare the residual standard deviation against the response's own scale
    # rather than against zero, so a genuinely tiny-but-real error still counts.
    y_scale = max(float(np.std(y_fit)), abs(float(np.mean(y_fit))), 1e-300)
    degenerate_fit = bool(
        error_source == "residual"
        and error_df > 0
        and math.sqrt(max(error_var, 0.0)) < DEGENERATE_RESIDUAL_REL_TOL * y_scale
    )

    # ---- Per-coefficient inference --------------------------------------
    verdict, warnings = _inference_verdict(error_df)
    if degenerate_fit:
        # Do NOT overwrite `inference`: a design can be both low-power and
        # degenerate, and the df-based verdict is still true. Degeneracy is
        # reported as its own flag plus a warning.
        warnings.append(
            "Degenerate fit: the model reproduces this response exactly, so the "
            "residual is at floating-point zero and there is no error left to "
            "test against. Effect sizes are still reported; p-values, t-statistics "
            "and confidence intervals are withheld because any value computed from "
            "this residual would be an artifact. Add replicate runs to obtain a "
            "measured pure-error term."
        )
    if error_df <= 0 and pure_error_df == 0 and cell_values is not None:
        warnings.append(
            "No replicated cells found: error estimate falls back to OLS "
            "residual. For a stochastic/LLM response, add replicate runs so "
            "significance rests on measured pure error, not model residual."
        )

    if error_df > 0:
        # SE_j = sqrt(error_var · (XᵀX)⁻¹_jj) on the SAME (observation-level
        # when replicated) matrix the coefficients were fit on, so SEs are
        # correct for balanced AND unbalanced replication.
        ses = doe_stats.coef_standard_errors(X_fit, error_var)
        t_crit = doe_stats.t_ppf(0.975, error_df)
    else:
        ses = np.full(p_terms, float("nan"))
        t_crit = float("nan")

    def _stat(idx: int) -> dict:
        coef = float(beta[idx])
        se = float(ses[idx])
        if error_df > 0 and se > 0 and not degenerate_fit:
            t = coef / se
            p = float(doe_stats.t_sf_two_sided(t, error_df))
            ci = [coef - t_crit * se, coef + t_crit * se]
            significant = p < 0.05
        else:
            t = p = float("nan")
            ci = [float("nan"), float("nan")]
            significant = None
        return {"se": se, "t": t, "p_value": p, "ci95": ci,
                "significant": significant}

    intercept_stats = _stat(0)
    main_stats = {labels[i][1]: _stat(i) for i in range(1, len(labels))
                  if labels[i][0] == "main"}
    inter_stats = {labels[i][1]: _stat(i) for i in range(1, len(labels))
                   if labels[i][0] == "inter"}

    return {
        "intercept": intercept,
        "main": main_effects,
        "interactions": inter_effects,
        "intercept_stats": intercept_stats,
        "main_stats": main_stats,
        "inter_stats": inter_stats,
        "r2": r2,
        "degenerate_fit": degenerate_fit,
        "n_runs": n,
        "n_factors": k,
        "residual_df": residual_df,
        "pure_error_df": pure_error_df,
        "error_df": error_df,
        "error_var": error_var,
        "error_source": error_source,
        "inference": verdict,
        "warnings": warnings,
    }


def _term_key(term: str) -> frozenset:
    """Reduce any term label to the set of factors it involves.

    Alias chains write interactions as "a\u00b7b"; ranked rows write them as
    "a \u00d7 b". Both mean the same column, so compare on the factor set.
    """
    parts = term.replace("\u00d7", "\u00b7").split("\u00b7")
    return frozenset(p.strip() for p in parts if p.strip())


def rank_findings(effects: dict, factor_names: list[str],
                  alias_chains: list[list[str]] | None = None) -> list[dict]:
    """Sort effects by absolute magnitude with human-readable labels.

    Each row carries the per-effect trust signals (se, t, p_value, ci95,
    significant) so a consumer ranking by magnitude can still see whether the
    top effect is statistically distinguishable from noise.

    When `alias_chains` is supplied, every row also carries `aliased_with`: the
    other effects sharing its column. In a low-resolution design several rows
    are the SAME estimate under different names, and a consumer filtering for
    `significant` would otherwise never learn that from the row itself.
    """
    chain_by_key: dict[frozenset, list[str]] = {}
    for chain in (alias_chains or []):
        for term in chain:
            chain_by_key[_term_key(term)] = list(chain)

    def _aliases(term: str):
        if not alias_chains:
            return None
        chain = chain_by_key.get(_term_key(term))
        if not chain:
            return []
        key = _term_key(term)
        return [t for t in chain if _term_key(t) != key]

    main_stats = effects.get("main_stats", {})
    inter_stats = effects.get("inter_stats", {})
    rows = []
    for idx, val in effects["main"].items():
        row = {
            "term": factor_names[idx],
            "kind": "main",
            "effect": val,
            "abs_effect": abs(val),
        }
        row.update(main_stats.get(idx, {}))
        row["aliased_with"] = _aliases(row["term"])
        rows.append(row)
    for (i, j), val in effects["interactions"].items():
        row = {
            "term": f"{factor_names[i]} × {factor_names[j]}",
            "kind": "interaction",
            "effect": val,
            "abs_effect": abs(val),
        }
        row.update(inter_stats.get((i, j), {}))
        row["aliased_with"] = _aliases(row["term"])
        rows.append(row)
    rows.sort(key=lambda r: -r["abs_effect"])
    return rows


# ---------------------------------------------------------------------------
# Practical significance, next step, prediction, confirmation
# ---------------------------------------------------------------------------

def _has_categorical(design_data: dict) -> bool:
    """True when any factor carries non-numeric levels (no center point exists)."""
    for f in design_data.get("factors", []):
        vals = f["levels"] if "levels" in f else [f.get("low"), f.get("high")]
        for v in vals:
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                try:
                    float(v)
                except (TypeError, ValueError):
                    return True
    return False


def _no_resolution(values) -> bool:
    """An objective whose observed range is zero across the whole design."""
    arr = np.asarray(values, dtype=float)
    return bool(arr.size > 0 and float(arr.max() - arr.min()) == 0.0)


NEXT_STEP_PRIORITY = {"decouple": 0, "add_replicates": 1, "confirm": 2,
                      "extend_range": 3, "stop_or_widen": 4}


def annotate_practical(rows: list[dict], min_effect: float | None) -> list[dict]:
    """Add `low_to_high_change` (= 2 x coefficient, the predicted change from the
    low to the high level) and `practically_significant` (|change| >= min_effect)
    to every ranked row. A statistically significant effect smaller than the
    smallest change worth shipping is still not worth shipping."""
    for r in rows:
        change = 2.0 * float(r["effect"])
        r["low_to_high_change"] = change
        r["practically_significant"] = (
            None if min_effect is None else bool(abs(change) >= float(min_effect))
        )
    return rows


def next_step(effects: dict, rows: list[dict], design: np.ndarray,
              factor_names: list[str], best_run_idx: int | None,
              min_effect: float | None = None, objective: str | None = None,
              has_categorical: bool = False, no_resolution: bool = False) -> list[dict]:
    """Decide what the measurements say to do next, in priority order.

    Rules (sequential-experimentation practice: screen -> decouple -> confirm ->
    move; Montgomery 2013, AFIT STAT COE):
      decouple        a significant effect shares its column with another term
      add_replicates  no error df / degenerate fit / low power: p-values are not
                      trustworthy until replicates supply an error estimate
      confirm         something moved the number: run >=3 (5-10) confirmation
                      runs at best_factors and check the prediction interval
      extend_range    a significant main effect with the best run at one end of
                      its range: the optimum may lie beyond it (steepest ascent)
      stop_or_widen   nothing moved the number beyond noise or min_effect
    """
    steps: list[dict] = []
    if no_resolution:
        # Every run measured the same value: this objective cannot distinguish
        # anything, so no rule below has evidence to fire on.
        return [{
            "action": "stop_or_widen", "terms": [], "objective": objective,
            "reason": ("This objective did not change across any run (zero observed "
                       "range). It has no resolution here: it can neither pick a winner "
                       "nor certify a guardrail. Use a finer-grained measurement, or a "
                       "wider factor range, before trusting it."),
        }]
    sig = [r for r in rows if r.get("significant")]
    practical = [r for r in rows if r.get("practically_significant")]

    aliased = [r for r in sig if r.get("aliased_with")]
    if aliased:
        terms = sorted({t for r in aliased for t in [r["term"], *r["aliased_with"]]})
        steps.append({
            "action": "decouple", "terms": terms, "objective": objective,
            "reason": (f"{len(aliased)} significant effect(s) share a column with "
                       f"other terms ({', '.join(terms)}): the same estimate under "
                       f"several names. Run a fold-over, or one-factor-at-a-time "
                       f"confirmation runs at these terms, before crediting any of them."),
        })

    error_df = int(effects.get("error_df", 0))
    verdict = str(effects.get("inference", ""))
    if error_df == 0 or effects.get("degenerate_fit") or verdict.startswith("low power"):
        n = 3 if error_df == 0 else 2
        why = ("the design is saturated (no error degrees of freedom)" if error_df == 0
               else "the residual is numerically zero" if effects.get("degenerate_fit")
               else f"only {error_df} error df")
        how = (f"replicate {n} design rows (repeat their run_id in results.jsonl)"
               if has_categorical else
               f"add {n} center-point runs, or replicate {n} design rows (repeat "
               f"their run_id in results.jsonl)")
        steps.append({
            "action": "add_replicates", "terms": [], "objective": objective,
            "reason": (f"{why}, so p-values cannot separate a real effect from a "
                       f"fluke. To obtain a pure-error estimate, {how}."),
        })

    has_signal = bool(sig) or bool(practical)
    if has_signal:
        steps.append({
            "action": "confirm", "terms": [], "objective": objective,
            "reason": ("At least one effect is real. Run >=3 (5-10 per Jensen 2016) "
                       "confirmation runs at best_factors and check that their mean "
                       "lies inside the model's prediction interval (`doe.py confirm`)."),
        })
        if best_run_idx is not None:
            for r in sig:
                if r.get("kind") != "main":
                    continue
                idx = factor_names.index(r["term"])
                level = float(design[best_run_idx, idx])
                side = "high" if level > 0 else "low"
                steps.append({
                    "action": "extend_range", "terms": [r["term"]], "objective": objective,
                    "reason": (f"{r['term']} is significant and the best run sits at its "
                               f"{side} level; the optimum may lie beyond the tested range. "
                               f"Move the {side} level further out (steepest ascent) in the "
                               f"next stage."),
                })
    elif error_df > 0 and not effects.get("degenerate_fit"):
        bar = (f"the practical threshold min_effect={min_effect:g}" if min_effect is not None
               else "noise")
        steps.append({
            "action": "stop_or_widen", "terms": [], "objective": objective,
            "reason": (f"No effect exceeded {bar}. Either the factors do not move this "
                       f"number (stop and record that), or the levels were too close "
                       f"together (widen them and re-run)."),
        })

    steps.sort(key=lambda st: NEXT_STEP_PRIORITY[st["action"]])
    return steps


def merge_next_steps(per_objective_steps: dict[str, list[dict]]) -> list[dict]:
    """Merge per-objective next steps into one ordered list without duplicates.
    `stop_or_widen` survives only when EVERY objective says so."""
    merged: list[dict] = []
    seen: set = set()
    n_obj = len(per_objective_steps)
    stops = sum(1 for steps in per_objective_steps.values()
                if any(st["action"] == "stop_or_widen" for st in steps))
    for name, steps in per_objective_steps.items():
        for st in steps:
            if st["action"] == "stop_or_widen" and stops < n_obj:
                continue
            key = (st["action"], tuple(st["terms"]))
            if key in seen:
                for m in merged:
                    if (m["action"], tuple(m["terms"])) == key:
                        m.setdefault("objectives", [])
                        if name not in m["objectives"]:
                            m["objectives"].append(name)
                continue
            seen.add(key)
            entry = dict(st)
            entry["objectives"] = [name]
            entry.pop("objective", None)
            merged.append(entry)
    merged.sort(key=lambda st: NEXT_STEP_PRIORITY[st["action"]])
    return merged


def _beta_vector(effects: dict, labels: list) -> np.ndarray:
    beta = [float(effects["intercept"])]
    for lab in labels[1:]:
        kind, key = lab
        beta.append(float(effects["main"][key] if kind == "main"
                          else effects["interactions"][key]))
    return np.asarray(beta, dtype=float)


def predict_at(effects: dict, design: np.ndarray, row_idx: int,
               include_interactions: bool,
               replicate_counts: list[int] | None = None) -> tuple[float, float]:
    """Model prediction and leverage h = x (X'X)^-1 x' at design row `row_idx`.
    Leverage uses the observation-level matrix when replicates were fitted."""
    X, labels = _design_matrix(design, include_interactions)
    beta = _beta_vector(effects, labels)
    x = X[row_idx]
    X_fit = np.repeat(X, replicate_counts, axis=0) if replicate_counts else X
    xtx_inv = np.linalg.pinv(X_fit.T @ X_fit)
    h = float(x @ xtx_inv @ x)
    return float(x @ beta), h


def confirm_objective(obj: dict, effects: dict, design: np.ndarray, best_idx: int,
                      include_interactions: bool, conf_values: list[float],
                      replicate_counts: list[int] | None, alpha: float,
                      design_values=()) -> dict:
    """Jensen (2016) style confirmation of one objective at the best run.

    Prediction interval for the MEAN of n confirmation runs:
        yhat +- t(1-alpha/2, df) * sqrt(s2 * (1/n + h))
    and for EACH run: sqrt(s2 * (1 + h)). s2 and df come from the design's error
    term when it has one; otherwise from the confirmation sample itself
    (pi_source = confirmation_sd) - weaker, and flagged.
    """
    name = obj["name"]
    direction = obj.get("direction", "lower")
    role = obj.get("role", "primary")
    n = len(conf_values)
    yhat, h = predict_at(effects, design, best_idx, include_interactions, replicate_counts)
    vals = np.asarray(conf_values, dtype=float)
    mean = float(vals.mean()) if n else float("nan")
    warnings: list[str] = []

    error_df = int(effects.get("error_df", 0))
    if error_df > 0 and not effects.get("degenerate_fit"):
        s2, df, pi_source = float(effects["error_var"]), error_df, effects.get("error_source", "residual")
    elif n >= 2:
        s2, df, pi_source = float(vals.var(ddof=1)), n - 1, "confirmation_sd"
        warnings.append(f"{name}: the design carries no usable error estimate; the "
                        f"prediction interval uses the confirmation sample SD "
                        f"({n} runs), which is weaker.")
    else:
        s2, df, pi_source = float("nan"), 0, "none"
        warnings.append(f"{name}: no error estimate at all (saturated design and "
                        f"{n} confirmation run); cannot judge agreement.")

    if df > 0 and n > 0:
        t = float(doe_stats.t_ppf(1 - alpha / 2, df))
        hw_mean = t * math.sqrt(max(s2, 0.0) * (1.0 / n + h))
        hw_each = t * math.sqrt(max(s2, 0.0) * (1.0 + h))
        pi_mean = [yhat - hw_mean, yhat + hw_mean]
        pi_each = [yhat - hw_each, yhat + hw_each]
        mean_in_pi = bool(pi_mean[0] <= mean <= pi_mean[1])
        all_in_pi = bool(all(pi_each[0] <= v <= pi_each[1] for v in vals))
    else:
        pi_mean = pi_each = [float("nan"), float("nan")]
        mean_in_pi = all_in_pi = None

    def meets(v: float, bar: float) -> bool:
        return v >= bar if direction == "higher" else v <= bar

    target = obj.get("target"); baseline = obj.get("baseline")
    min_acceptable = obj.get("min_acceptable"); min_effect = obj.get("min_effect")
    bar_desc, passed, why = None, None, ""
    if role == "primary":
        if target is not None:
            bar_desc = f"target {float(target):g}"
            passed = meets(mean, float(target))
            why = f"mean {mean:.6g} {'meets' if passed else 'misses'} {bar_desc}"
        elif baseline is not None and min_effect is not None:
            improvement = (float(baseline) - mean) if direction == "lower" else (mean - float(baseline))
            bar_desc = f"improve on baseline {float(baseline):g} by >= {float(min_effect):g}"
            passed = improvement >= float(min_effect)
            why = f"improvement {improvement:.6g} vs min_effect {float(min_effect):g}"
        else:
            bar_desc = "unspecified (no target, no baseline+min_effect)"
            passed = bool(mean_in_pi) if mean_in_pi is not None else False
            why = "no bar declared; agreement with the model is the only check"
            warnings.append(f"{name}: primary objective has no target and no "
                            f"baseline+min_effect; model agreement alone cannot authorize promotion.")
        if passed and mean_in_pi is False:
            passed = False
            why += "; but the confirmation mean lies outside the prediction interval"
    elif role == "guardrail":
        bar = min_acceptable if min_acceptable is not None else baseline
        if bar is None:
            bar_desc, passed, why = "none declared", None, "guardrail without a bar constrains nothing"
        else:
            bar_desc = f"{'min_acceptable' if min_acceptable is not None else 'baseline'} {float(bar):g}"
            passed = meets(mean, float(bar))
            why = f"mean {mean:.6g} {'holds' if passed else 'breaks'} {bar_desc}"
    else:
        bar_desc, passed, why = "reported only", None, "quality metric; never decides"

    return {
        "name": name, "role": role, "direction": direction,
        "resolution": "none" if _no_resolution(design_values) else "ok",
        "predicted": yhat, "leverage": h,
        "n_confirmation": n, "confirmation_values": [float(v) for v in vals],
        "confirmation_mean": mean,
        "pi_source": pi_source, "pi_df": df, "alpha": alpha,
        "pi_mean": pi_mean, "pi_each": pi_each,
        "mean_in_pi": mean_in_pi, "all_in_pi": all_in_pi,
        "bar": bar_desc, "pass": passed, "why": why,
        "threshold_check": "point_estimate", "inference_claim": "model_agreement_only",
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Level mapping (-1/+1 coding ↔ user-specified levels)
# ---------------------------------------------------------------------------

def validate_levels(factors: list[dict]) -> None:
    """The current design engine supports exactly two concrete levels."""
    names = set()
    for f in factors:
        if not isinstance(f, dict) or not isinstance(f.get("name"), str) or not f["name"].strip():
            raise ValueError("each factor requires a nonempty name")
        if f["name"] in names:
            raise ValueError(f"duplicate factor name: {f['name']}")
        names.add(f["name"])
        if "levels" in f:
            if not isinstance(f["levels"], list) or len(f["levels"]) != 2:
                raise ValueError(f"factor {f['name']}: unsupported levels; exactly two levels "
                                 "are required (3+ level categorical designs are unsupported)")
            if "low" in f or "high" in f:
                raise ValueError(f"factor {f['name']}: use levels or low/high, not both")
            values = f["levels"]
        elif "low" in f and "high" in f:
            values = [f["low"], f["high"]]
        else:
            raise ValueError(f"factor {f['name']}: supply two levels or low/high")
        if values[0] == values[1]:
            raise ValueError(f"factor {f['name']}: levels must differ")
        if any(not isinstance(v, (str, int, float, bool)) or
               isinstance(v, float) and not math.isfinite(v) for v in values):
            raise ValueError(f"factor {f['name']}: levels must be finite scalars")


def map_levels(design: np.ndarray, factors: list[dict]) -> list[dict]:
    """Convert ±1 coded design into named runs with concrete values.
    factors[i] = {"name": str, "low": <value>, "high": <value>} OR
    factors[i] = {"name": str, "levels": [<low>, <high>]}."""
    validate_levels(factors)
    runs = []
    for run_idx, row in enumerate(design):
        run = {"_run_id": run_idx, "_factors": {}}
        for col_idx, coded in enumerate(row):
            f = factors[col_idx]
            if "low" in f and "high" in f:
                value = f["high"] if coded > 0 else f["low"]
            elif "levels" in f and len(f["levels"]) == 2:
                value = f["levels"][1] if coded > 0 else f["levels"][0]
            else:
                raise ValueError(f"factor {f['name']}: missing two concrete levels")
            run["_factors"][f["name"]] = value
        runs.append(run)
    return runs


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_generate(args: argparse.Namespace) -> int:
    factors = _read_json_arg(args.factors)
    if not isinstance(factors, list) or not factors:
        sys.stderr.write("--factors must be a JSON list of {name, low, high} or {name, levels}\n")
        return 2
    try:
        validate_levels(factors)
    except ValueError as exc:
        sys.stderr.write(f"factors error: {exc}\n")
        return 2
    k = len(factors)
    design_type = args.design
    if design_type == "auto":
        design_type = select_design(k)
    if design_type == "autoresearch":
        sys.stderr.write(f"k={k}: defer to autoresearch (single-variable case)\n")
        return 3
    matrix, name = build_design(k, design_type)
    runs = map_levels(matrix, factors)
    rng = np.random.default_rng(args.seed)
    order = list(range(len(runs)))
    rng.shuffle(order)
    aliasing = alias_structure(matrix, factor_names=[f["name"] for f in factors])
    output = {
        "design": {"type": design_type, "name": name, "n_runs": len(runs), "n_factors": k},
        "factors": factors,
        "matrix": matrix.tolist(),
        "run_order": order,
        "runs": runs,
        "aliasing": aliasing,
    }
    print(json.dumps(output, indent=2))
    return 0


def _read_json_arg(raw: str):
    if raw.lstrip().startswith(("[", "{")):
        return json.loads(raw)
    return json.loads(Path(raw).read_text())


def _load_objectives_arg(arg_objectives: str | None, arg_selection: str | None
                         ) -> tuple[list[dict] | None, str]:
    """Parse --objectives value: JSON inline, path to file, or None.

    Accepts:
      - bare list:  '[{"name":"x","direction":"lower","weight":1}]'
      - envelope:   '{"objectives":[...], "selection":"scalarize"}'
      - path to either of the above

    Returns (objectives_list_or_None, selection_method).
    --selection overrides any "selection" key in the file/envelope.
    """
    if arg_objectives is None:
        return None, arg_selection or "scalarize"

    parsed = _read_json_arg(arg_objectives)

    if isinstance(parsed, list):
        obj_list = parsed
        file_selection = "scalarize"
    elif isinstance(parsed, dict):
        obj_list = parsed.get("objectives", [])
        file_selection = parsed.get("selection", "scalarize")
    else:
        raise ValueError("--objectives must be a JSON list or {objectives:[...], selection:...}")

    selection = arg_selection or file_selection
    return obj_list, selection


def _run_id(row: dict, n: int) -> int:
    rid = row.get("run_id")
    if type(rid) is not int or not 0 <= rid < n:
        raise ValueError(f"run_id {rid!r} must be an integer in [0,{n})")
    return rid


def _cell_guard(rows: list[dict]) -> bool | None:
    states = [objectives.guard_status(row) for row in rows]
    if False in states:
        return False
    return True if states and all(s is True for s in states) else None


def _single_mean_rows(lines: list[str], y: np.ndarray) -> list[dict]:
    rows = [json.loads(line) for line in lines if line.strip()]
    means = []
    for i, value in enumerate(y):
        row = {"run_id": i, "values": {"value": float(value)}}
        guard = _cell_guard([r for r in rows if r["run_id"] == i])
        if guard is not None:
            row["guard_ok"] = guard
        means.append(row)
    return means


def _collect_single_metric(lines: list[str], n: int
                           ) -> tuple[np.ndarray, list[list[float]], int]:
    """Parse legacy {run_id, value} JSONL, grouping replicates by run_id.

    Multiple rows with the same run_id are replicate measurements of the same
    design cell. Returns:
      y            - per-cell mean response, length n (used for effect fitting)
      cell_values  - cell_values[i] = list of replicate values at run i
      total_obs    - total number of result rows seen

    Requires that every run_id in [0, n) is observed at least once.
    """
    cells: dict[int, list[float]] = {i: [] for i in range(n)}
    total_obs = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        objectives.guard_status(row)
        rid = _run_id(row, n)
        if rid not in cells:
            raise ValueError(f"run_id {rid} out of range [0,{n})")
        val = objectives.finite_measurement(row["value"])
        if not math.isfinite(val):
            raise ValueError(f"run_id {rid}: non-finite value {val!r}")
        cells[rid].append(val)
        total_obs += 1
    missing = [i for i in range(n) if not cells[i]]
    if missing:
        raise ValueError(f"no results for run_id(s) {missing}")
    cell_values = [cells[i] for i in range(n)]
    y = np.array([float(np.mean(cells[i])) for i in range(n)])
    return y, cell_values, total_obs


def _collect_multi_metric(rows: list[dict], obj_names: list[str], n: int
                          ) -> tuple[dict[str, np.ndarray], dict[str, list[list[float]]],
                                     dict[int, dict], int]:
    """Group multi-objective result rows by run_id, supporting replicates.

    Returns per-objective (y_mean vector, cell_values), a representative row per
    run_id (cell-mean values, for selection), and the total observation count.
    """
    cells: dict[int, list[dict]] = {i: [] for i in range(n)}
    total_obs = 0
    for row in rows:
        objectives.guard_status(row)
        rid = _run_id(row, n)
        if rid not in cells:
            raise ValueError(f"run_id {rid} out of range [0,{n})")
        cells[rid].append(row)
        total_obs += 1
    missing = [i for i in range(n) if not cells[i]]
    if missing:
        raise ValueError(f"no results for run_id(s) {missing}")

    def _finite(v: float, rid: int, name: str) -> float:
        fv = objectives.finite_measurement(v)
        if not math.isfinite(fv):
            raise ValueError(f"run_id {rid}, objective '{name}': non-finite value {fv!r}")
        return fv

    y_by_obj: dict[str, np.ndarray] = {}
    cellvals_by_obj: dict[str, list[list[float]]] = {}
    for name in obj_names:
        per_cell_vals = [
            [_finite(r["values"][name], i, name) for r in cells[i]] for i in range(n)
        ]
        cellvals_by_obj[name] = per_cell_vals
        y_by_obj[name] = np.array([float(np.mean(v)) for v in per_cell_vals])

    # Representative (cell-mean) row per run_id for objectives.select_best.
    mean_rows: dict[int, dict] = {}
    for i in range(n):
        mean_rows[i] = {
            "run_id": i,
            "values": {name: float(np.mean([float(r["values"][name]) for r in cells[i]]))
                       for name in obj_names},
        }
        state = _cell_guard(cells[i])
        if state is not None:
            mean_rows[i]["guard_ok"] = state
    return y_by_obj, cellvals_by_obj, mean_rows, total_obs


def _read_design(path: str) -> tuple[dict, np.ndarray]:
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict):
        raise ValueError("design must be an object")
    try:
        matrix = np.array(data.get("matrix"), dtype=float)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("design matrix must contain finite two-level numbers") from exc
    if (matrix.ndim != 2 or not all(matrix.shape) or not np.isfinite(matrix).all()
            or not np.isin(matrix, [-1, 1]).all()):
        raise ValueError("design matrix must be a nonempty two-dimensional array of -1/+1 values")
    factors = data.get("factors")
    if not isinstance(factors, list) or len(factors) != matrix.shape[1]:
        raise ValueError("design factors must match the matrix columns")
    if any(not isinstance(f, dict) or not isinstance(f.get("name"), str)
           or not f["name"].strip() for f in factors):
        raise ValueError("design factors require nonempty names")
    if len({f["name"] for f in factors}) != len(factors):
        raise ValueError("design factor names must be unique")
    return data, matrix


def cmd_analyze(args: argparse.Namespace) -> int:
    design_data, matrix = _read_design(args.design)
    factor_names = [f["name"] for f in design_data["factors"]]
    n = matrix.shape[0]
    k = matrix.shape[1]
    include_interactions = (k <= 3)

    # ------------------------------------------------------------------
    # Determine mode: multi-objective or legacy single-metric
    # ------------------------------------------------------------------
    try:
        obj_list, selection = _load_objectives_arg(
            getattr(args, "objectives", None),
            getattr(args, "selection", None),
        )
    except Exception as exc:
        sys.stderr.write(f"--objectives parse error: {exc}\n")
        return 2

    aliasing = alias_structure(matrix, factor_names=factor_names)

    if obj_list is None:
        # ---- Backward-compatible single-metric path (now replicate-aware) ---
        try:
            y, cell_values, total_obs = _collect_single_metric(
                Path(args.results).read_text().splitlines(), n
            )
        except (ValueError, KeyError) as exc:
            sys.stderr.write(f"results parse error: {exc}\n")
            return 2
        n_replicated = sum(1 for c in cell_values if len(c) > 1)
        effects = fit_effects(matrix, y, include_interactions=include_interactions,
                              cell_values=cell_values)
        findings = rank_findings(effects, factor_names,
                                 alias_chains=aliasing.get("alias_chains"))
        min_effect = getattr(args, "min_effect", None)
        findings = annotate_practical(findings, min_effect)
        direction = args.direction or "lower"
        selection_result = objectives.select_best(
            _single_mean_rows(Path(args.results).read_text().splitlines(), y),
            [{"name": "value", "direction": direction}])
        best_run_idx = selection_result["best_run_id"]
        steps = [] if best_run_idx is None else next_step(effects, findings, matrix, factor_names, best_run_idx,
                          min_effect=min_effect,
                          has_categorical=_has_categorical(design_data),
                          no_resolution=_no_resolution(y))
        best_factors: dict | None = None
        runs_block = design_data.get("runs")
        if best_run_idx is not None and isinstance(runs_block, list) and 0 <= best_run_idx < len(runs_block):
            candidate = runs_block[best_run_idx].get("_factors")
            if isinstance(candidate, dict):
                best_factors = candidate
        output = {
            "summary": {
                "design_type": design_data["design"]["type"],
                "n_runs": n,
                "n_factors": k,
                "n_observations": total_obs,
                "n_replicated_cells": n_replicated,
                "r2": effects["r2"],
                "intercept": effects["intercept"],
                "intercept_stats": effects["intercept_stats"],
                "residual_df": effects["residual_df"],
                "pure_error_df": effects["pure_error_df"],
                "error_df": effects["error_df"],
                "error_source": effects["error_source"],
                "inference": effects["inference"],
            },
            "warnings": effects["warnings"] + selection_result["warnings"],
            "selection": selection_result,
            "analysis_scope": "descriptive_all_measurements_including_failed_guards",
            "aliasing": aliasing,
            "ranked_effects": findings,
            "best_run": best_run_idx,
            "best_value": float(y[best_run_idx]) if best_run_idx is not None else None,
            "direction": direction,
            "min_effect": min_effect,
            "next_step": steps,
        }
        if best_factors is not None:
            output["best_factors"] = best_factors
        print(json.dumps(output, indent=2))
        return 0

    contract = objectives.validate_objectives(obj_list)
    if contract["errors"] or not obj_list:
        sys.stderr.write(f"objectives contract error: {contract['errors'] or 'No objectives'}\n")
        return 2
    # ---- Multi-objective path ------------------------------------------
    # Read results JSONL: each line is {"run_id": i, "values": {...}, "guard_ok": bool}
    # Also accept legacy {"run_id": i, "value": n} when exactly one objective declared.
    single_obj_name: str | None = obj_list[0]["name"] if len(obj_list) == 1 else None

    raw_results: list[dict] = []
    for line in Path(args.results).read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        # Legacy single-value line with one declared objective
        if "value" in row and "values" not in row:
            if single_obj_name is None:
                sys.stderr.write(
                    f"Legacy {{run_id, value}} line found but >1 objectives declared; "
                    f"cannot map 'value' to objective name\n"
                )
                return 2
            row = {**row, "values": {single_obj_name: row["value"]}}
        raw_results.append(row)

    obj_names = [o["name"] for o in obj_list]
    try:
        y_by_obj, cellvals_by_obj, mean_rows, total_obs = _collect_multi_metric(
            raw_results, obj_names, n
        )
    except (ValueError, KeyError) as exc:
        sys.stderr.write(f"results parse error: {exc}\n")
        return 2

    # Per-objective effects analysis (replicate-aware: pure error per objective)
    per_objective: dict[str, dict] = {}
    steps_by_obj: dict[str, list[dict]] = {}
    any_replicated = False
    contract = objectives.validate_objectives(obj_list)
    if contract["errors"]:
        for e in contract["errors"]:
            sys.stderr.write(f"objectives contract error: {e}\n")
        return 2
    for obj in obj_list:
        obj_name = obj["name"]
        direction = obj.get("direction", "lower")
        cell_values = cellvals_by_obj[obj_name]
        if any(len(c) > 1 for c in cell_values):
            any_replicated = True
        eff = fit_effects(matrix, y_by_obj[obj_name],
                          include_interactions=include_interactions,
                          cell_values=cell_values)
        findings = rank_findings(eff, factor_names,
                                 alias_chains=aliasing.get("alias_chains"))
        findings = annotate_practical(findings, obj.get("min_effect"))
        y_obj = y_by_obj[obj_name]
        obj_best = int(np.argmin(y_obj)) if direction == "lower" else int(np.argmax(y_obj))
        unresolved = _no_resolution(y_obj)
        steps_by_obj[obj_name] = next_step(eff, findings, matrix, factor_names, obj_best,
                                           min_effect=obj.get("min_effect"),
                                           objective=obj_name,
                                           has_categorical=_has_categorical(design_data),
                                           no_resolution=unresolved)
        if unresolved:
            eff["warnings"] = list(eff.get("warnings", [])) + [
                f"{obj_name}: identical value in every run (zero observed range). "
                f"This objective has no resolution in this design"
                + (" and cannot certify the guardrail it represents."
                   if obj.get("role") == "guardrail" else ".")]
        per_objective[obj_name] = {
            "ranked_effects": findings,
            "resolution": "none" if unresolved else "ok",
            "min_effect": obj.get("min_effect"),
            "next_step": steps_by_obj[obj_name],
            "r2": eff["r2"],
            "degenerate_fit": eff.get("degenerate_fit", False),
            "intercept": eff["intercept"],
            "intercept_stats": eff["intercept_stats"],
            "direction": direction,
            "residual_df": eff["residual_df"],
            "pure_error_df": eff["pure_error_df"],
            "error_df": eff["error_df"],
            "error_source": eff["error_source"],
            "inference": eff["inference"],
            "warnings": eff["warnings"],
        }

    # Build runs list for objectives.select_best (cell means when replicated)
    runs_for_selection = [mean_rows[i] for i in range(n)]

    try:
        sel_result = objectives.select_best(runs_for_selection, obj_list, selection)
    except Exception as exc:
        sys.stderr.write(f"objectives.select_best error: {exc}\n")
        return 2

    best_run_id = sel_result["best_run_id"]

    # Pull concrete factor levels for best run
    best_factors_multi: dict | None = None
    runs_block = design_data.get("runs")
    if (best_run_id is not None and isinstance(runs_block, list)
            and 0 <= best_run_id < len(runs_block)):
        candidate = runs_block[best_run_id].get("_factors")
        if isinstance(candidate, dict):
            best_factors_multi = candidate

    output = {
        "summary": {
            "design_type": design_data["design"]["type"],
            "n_runs": n,
            "n_factors": k,
            "n_observations": total_obs,
            "replicated": any_replicated,
            "selection": selection,
        },
        "aliasing": aliasing,
        "objectives_contract": contract,
        "analysis_scope": "descriptive_all_measurements_including_failed_guards",
        "per_objective": per_objective,
        "selection": sel_result,
        "best_run": best_run_id,
        "next_step": merge_next_steps(steps_by_obj),
    }
    if best_factors_multi is not None:
        output["best_factors"] = best_factors_multi

    print(json.dumps(output, indent=2))
    return 0


def _read_confirmation(path: str, obj_names: list[str]) -> dict[str, list[float]]:
    """confirm.jsonl rows: {"values": {...}} or {"value": n} (single objective)."""
    out: dict[str, list[float]] = {n: [] for n in obj_names}
    single = obj_names[0] if len(obj_names) == 1 else None
    seen_attempts = set()
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        objectives.guard_status(row)
        if "attempt_id" in row:
            attempt = row["attempt_id"]
            if not isinstance(attempt, str) or not attempt.strip():
                raise ValueError("confirmation attempt_id must be a nonempty string")
            if attempt in seen_attempts:
                raise ValueError(f"duplicate confirmation attempt_id: {attempt}")
            seen_attempts.add(attempt)
        if "values" in row:
            vals = row["values"]
        elif "value" in row and single is not None:
            vals = {single: row["value"]}
        else:
            raise ValueError("confirmation row needs 'values' (or 'value' with one objective)")
        for n in obj_names:
            if n not in vals:
                raise ValueError(f"confirmation row missing objective '{n}'")
            v = objectives.finite_measurement(vals[n])
            if not math.isfinite(v):
                raise ValueError(f"non-finite confirmation value for '{n}'")
            out[n].append(v)
    if not any(out.values()):
        raise ValueError("confirmation requires at least one measurement")
    return out


def promotion_checks(contract: dict | None, design: dict, screening: list[dict],
                     confirmation: list[dict], best_idx: int, obj_list: list[dict],
                     selection: str) -> dict:
    """Check supplied provenance attestations; this does not authenticate a reviewer.

    schema_version=1 binds candidate {run_id,candidate_id,config_hash}, fixture_id,
    scorer_id, objectives and selection to a design run and its measured rows.
    split requires screening_id, confirmation_id and independent=true. measurement
    requires valid=true/evidence; review requires approved=true/reviewer/evidence.
    Optional item_ids on rows must be disjoint between the two splits.
    """
    blockers = []
    if contract is None:
        return {"ready": False, "blockers": ["No promotion contract supplied"],
                "provenance": "unverified_attestations"}
    if not isinstance(contract, dict):
        return {"ready": False, "blockers": ["contract must be an object"],
                "provenance": "unverified_attestations"}
    if type(contract.get("schema_version")) is not int or contract["schema_version"] != 1:
        blockers.append("Unsupported contract schema_version (expected 1)")
    def nonempty(value):
        return isinstance(value, str) and bool(value.strip())
    candidate = contract.get("candidate")
    candidate = candidate if isinstance(candidate, dict) else {}
    if type(candidate.get("run_id")) is not int or candidate.get("run_id") != best_idx:
        blockers.append("contract candidate.run_id does not match selected run")
    for field in ("candidate_id", "config_hash"):
        if not nonempty(candidate.get(field)):
            blockers.append(f"candidate.{field} is required")
    for field in ("fixture_id", "scorer_id"):
        if not nonempty(contract.get(field)):
            blockers.append(f"{field} is required")
    objective_validation = objectives.validate_objectives(contract.get("objectives"))
    if objective_validation["errors"]:
        blockers.extend(f"contract objectives: {error}" for error in objective_validation["errors"])
    if contract.get("objectives") != obj_list:
        blockers.append("contract objectives do not match analysis objectives")
    if contract.get("selection") != selection:
        blockers.append("contract selection does not match analysis selection")
    for obj in obj_list:
        role = obj.get("role", "primary")
        if role == "primary" and obj.get("target") is None and not (
                obj.get("baseline") is not None and obj.get("min_effect") is not None):
            blockers.append(f"{obj['name']}: primary requires target or baseline+min_effect")
        if role == "guardrail" and obj.get("min_acceptable") is None and obj.get("baseline") is None:
            blockers.append(f"{obj['name']}: guardrail requires an absolute bar or baseline")
    split = contract.get("split")
    split = split if isinstance(split, dict) else {}
    if split.get("independent") is not True:
        blockers.append("independent split attestation is required")
    if not all(nonempty(split.get(k)) for k in ("screening_id", "confirmation_id")):
        blockers.append("screening and confirmation split identities are required")
    elif split["screening_id"] == split["confirmation_id"]:
        blockers.append("confirmation split must differ from screening split")
    for section, flag in (("measurement", "valid"), ("review", "approved")):
        record = contract.get(section)
        record = record if isinstance(record, dict) else {}
        if record.get(flag) is not True or not nonempty(record.get("evidence")):
            blockers.append(f"{section} requires {flag}=true and nonempty evidence")
        if section == "review" and not nonempty(record.get("reviewer")):
            blockers.append("review.reviewer is required")
    runs = design.get("runs", [])
    binding = runs[best_idx] if isinstance(runs, list) and best_idx < len(runs) else {}
    if not isinstance(binding, dict):
        binding = {}
    if (type(binding.get("_run_id")) is not int or binding.get("_run_id") != best_idx
            or sum(isinstance(r, dict) and r.get("_run_id") == best_idx for r in runs) != 1):
        blockers.append("selected design run must have one matching _run_id at its matrix index")
    try:
        factors = design.get("factors")
        matrix = np.asarray(design.get("matrix"), dtype=float)
        if not isinstance(factors, list) or not factors or matrix.ndim != 2 or matrix.shape[1] != len(factors):
            raise ValueError("factor declarations must match matrix columns")
        if not np.isfinite(matrix).all() or not np.isin(matrix, [-1, 1]).all():
            raise ValueError("matrix must contain only finite -1/+1 values")
        expected = map_levels(matrix[best_idx:best_idx + 1], factors)[0]["_factors"]
        actual_json = json.dumps(binding.get("_factors"), sort_keys=True, allow_nan=False)
        expected_json = json.dumps(expected, sort_keys=True, allow_nan=False)
        if actual_json != expected_json:
            blockers.append("selected design _factors do not match declared levels and matrix")
    except (ValueError, TypeError, IndexError, OverflowError) as exc:
        blockers.append(f"cannot validate selected design factor binding: {exc}")
    for field in ("candidate_id", "config_hash"):
        if not nonempty(binding.get(field)) or binding.get(field) != candidate.get(field):
            blockers.append(f"selected design run {field} does not match contract")
    semantic_config = contract.get("semantic_assessor", {"required": False})
    if not isinstance(semantic_config, dict) or type(semantic_config.get("required")) is not bool:
        blockers.append("semantic_assessor.required must be a boolean")
        semantic_config = {"required": True}
    items = {"screening": set(), "confirmation": set()}
    for phase, rows in (("screening", screening), ("confirmation", confirmation)):
        for i, row in enumerate(rows):
            label = f"{phase} row {i}"
            if phase == "confirmation" and not nonempty(row.get("attempt_id")):
                blockers.append(f"{label}: attempt_id is required for promotion")
            if phase == "screening" and objectives.guard_status(row) is not True:
                blockers.append(f"{label}: every screening guard must pass because the model uses all rows")
            for field in ("fixture_id", "scorer_id"):
                if row.get(field) != contract.get(field) or not nonempty(row.get(field)):
                    blockers.append(f"{label}: {field} mismatch or missing")
            if row.get("split_id") != split.get(phase + "_id") or not nonempty(row.get("split_id")):
                blockers.append(f"{label}: split_id mismatch or missing")
            if phase == "confirmation" or row.get("run_id") == best_idx:
                if type(row.get("run_id")) is not int or row.get("run_id") != best_idx:
                    blockers.append(f"{label}: run_id does not match selected run")
                for field in ("candidate_id", "config_hash"):
                    if not nonempty(row.get(field)) or row.get(field) != candidate.get(field):
                        blockers.append(f"{label}: {field} mismatch or missing")
                if objectives.guard_status(row) is not True:
                    blockers.append(f"{label}: guard must explicitly pass")
                if semantic_config["required"] and not objectives.semantic_measurement_available(
                        row.get("semantic_assessment")):
                    blockers.append(f"{label}: required semantic measurement is unavailable")
            if "item_ids" in row:
                ids = row["item_ids"]
                if not isinstance(ids, list) or not ids or not all(nonempty(v) for v in ids):
                    blockers.append(f"{label}: item_ids must be nonempty strings")
                else:
                    items[phase].update(ids)
    if items["screening"] & items["confirmation"]:
        blockers.append("screening and confirmation item_ids overlap")
    return {"ready": not blockers, "blockers": blockers,
            "provenance": "unverified_attestations"}


def cmd_confirm(args: argparse.Namespace) -> int:
    """Judge whether the best run is confirmed and whether the goal contract is met."""
    design_data, matrix = _read_design(args.design)
    factor_names = [f["name"] for f in design_data["factors"]]
    n = matrix.shape[0]
    k = matrix.shape[1]
    include_interactions = (k <= 3)
    alpha = float(args.alpha)
    if not math.isfinite(alpha) or not 0 < alpha < 1:
        sys.stderr.write("alpha must be finite and strictly between 0 and 1\n")
        return 2

    try:
        obj_list, selection = _load_objectives_arg(getattr(args, "objectives", None),
                                                   getattr(args, "selection", None))
    except Exception as exc:
        sys.stderr.write(f"--objectives parse error: {exc}\n")
        return 2

    single_metric = obj_list is None
    if single_metric:
        obj_list = [{"name": "value", "direction": args.direction or "lower",
                     "role": "primary", "weight": 1.0,
                     "target": args.target, "baseline": args.baseline,
                     "min_effect": args.min_effect}]
        selection = "scalarize"
    validation = objectives.validate_objectives(obj_list)
    if validation["errors"] or not obj_list:
        sys.stderr.write(f"objectives contract error: {validation['errors'] or 'No objectives'}\n")
        return 2
    obj_names = [o["name"] for o in obj_list]

    # Re-collect the design's results and refit per objective.
    raw_lines = Path(args.results).read_text().splitlines()
    try:
        if single_metric:
            y, cell_values, _ = _collect_single_metric(raw_lines, n)
            y_by_obj = {"value": y}
            cellvals_by_obj = {"value": cell_values}
            mean_rows = _single_mean_rows(raw_lines, y)
        else:
            raw_results = []
            for line in raw_lines:
                line = line.strip()
                if line:
                    row = json.loads(line)
                    if "value" in row and "values" not in row and len(obj_names) == 1:
                        row = {**row, "values": {obj_names[0]: row["value"]}}
                    raw_results.append(row)
            y_by_obj, cellvals_by_obj, mean_rows, _ = _collect_multi_metric(
                raw_results, obj_names, n)
        conf = _read_confirmation(args.confirmation, obj_names)
    except (ValueError, KeyError) as exc:
        sys.stderr.write(f"results parse error: {exc}\n")
        return 2

    sel = objectives.select_best([mean_rows[i] for i in range(n)], obj_list, selection)
    best_idx = sel["best_run_id"]
    if best_idx is None:
        print(json.dumps({"done": False, "numerical_confirmed": False,
                          "promotion_ready": False, "recommendation": "re_plan",
                          "best_run": None, "best_factors": None,
                          "n_confirmation": min(len(v) for v in conf.values()),
                          "alpha": alpha, "criteria": [],
                          "promotion_checks": {"ready": False,
                              "blockers": ["No eligible candidate"],
                              "provenance": "unverified_attestations"},
                          "inference_claim": "point_thresholds_and_model_agreement_only",
                          "reason": sel.get("reason"),
                          "warnings": sel.get("warnings", [])}, indent=2))
        return 0

    criteria = []
    warnings: list[str] = list(sel.get("warnings", []))
    n_conf = min(len(v) for v in conf.values()) if conf else 0
    unresolved_guardrails: list[str] = []
    for obj in obj_list:
        if obj.get("role") == "guardrail" and _no_resolution(y_by_obj[obj["name"]]):
            unresolved_guardrails.append(obj["name"])
            warnings.append(f"{obj['name']}: guardrail measured the same value in every "
                            f"design run. A metric with no resolution cannot certify that "
                            f"nothing degraded; measure something finer-grained.")
    if n_conf < 3:
        warnings.append(f"Only {n_conf} confirmation run(s); Jensen (2016) recommends 5-10 "
                        f"at the optimum. Treat any verdict as provisional.")
    for obj in obj_list:
        name = obj["name"]
        cv = cellvals_by_obj[name]
        rc = [len(c) for c in cv] if any(len(c) > 1 for c in cv) else None
        eff = fit_effects(matrix, y_by_obj[name], include_interactions=include_interactions,
                          cell_values=cv)
        row = confirm_objective(obj, eff, matrix, best_idx, include_interactions,
                                conf[name], rc, alpha, design_values=y_by_obj[name])
        warnings.extend(row.pop("warnings"))
        criteria.append(row)

    primaries = [c for c in criteria if c["role"] == "primary"]
    guardrails = [c for c in criteria if c["role"] == "guardrail"]
    primary_ok = bool(primaries) and all(c["pass"] is True for c in primaries)
    guardrails_ok = all(c["pass"] is not False for c in guardrails)
    numerical_confirmed = bool(primary_ok and guardrails_ok and n_conf >= 3 and not unresolved_guardrails)
    contract_arg = getattr(args, "contract", None)
    contract = _read_json_arg(contract_arg) if contract_arg else None
    screening = [json.loads(line) for line in raw_lines if line.strip()]
    confirmation = [json.loads(line) for line in Path(args.confirmation).read_text().splitlines()
                    if line.strip()]
    numerical_guard_failures = [f"{phase} row {i}" for phase, rows in
                                (("screening", screening), ("confirmation", confirmation))
                                for i, row in enumerate(rows) if objectives.guard_status(row) is False]
    if numerical_guard_failures:
        numerical_confirmed = False
        warnings.append("Failed execution or required measurement guards contaminate the fitted "
                        "model or confirmation sample. Numeric results are descriptive only; "
                        "collect a valid screening and confirmation batch before confirming.")
    promotion = promotion_checks(contract, design_data, screening, confirmation,
                                 best_idx, obj_list, selection)
    if any("attempt_id" not in row for row in confirmation):
        warnings.append("Confirmation rows without attempt_id cannot be deduplicated and "
                        "cannot authorize promotion; row count does not establish independent replication.")
    done = numerical_confirmed and promotion["ready"]
    if not primaries:
        warnings.append("No primary objective: nothing was required to improve, so 'done' "
                        "cannot be true.")
    if n_conf < 3:
        recommendation = "more_confirmation_runs"
    elif done:
        recommendation = "ship"
    elif numerical_confirmed:
        recommendation = "complete_promotion_contract"
    elif primary_ok and guardrails_ok and unresolved_guardrails:
        recommendation = "improve_guardrail_resolution"
    else:
        recommendation = "re_plan"

    best_factors = None
    runs_block = design_data.get("runs")
    if isinstance(runs_block, list) and 0 <= best_idx < len(runs_block):
        best_factors = runs_block[best_idx].get("_factors")

    output = {
        "done": done,
        "numerical_confirmed": numerical_confirmed,
        "numerical_guard_failures": numerical_guard_failures,
        "analysis_scope": "descriptive_all_measurements_including_failed_guards",
        "contenders": sel.get("contenders", []),
        "promotion_ready": done,
        "promotion_checks": promotion,
        "inference_claim": "point_thresholds_and_model_agreement_only",
        "independence": "caller_attested; unique attempt IDs do not prove independent units",
        "recommendation": recommendation,
        "best_run": int(best_idx),
        "best_factors": best_factors,
        "n_confirmation": n_conf,
        "alpha": alpha,
        "criteria": criteria,
        "unresolved_guardrails": unresolved_guardrails,
        "warnings": warnings,
    }
    print(json.dumps(output, indent=2))
    return 0


def cmd_detect(args: argparse.Namespace) -> int:
    try:
        k = int(args.factor_count)
    except ValueError:
        sys.stderr.write("factor-count must be an integer\n")
        return 2
    design_type = select_design(k)
    print(json.dumps({"factor_count": k, "design": design_type}, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    gen = sub.add_parser("generate", help="generate a DOE matrix")
    gen.add_argument("--factors", required=True,
                     help='JSON list or path to file: [{"name":"x1","low":1,"high":3}, ...]')
    gen.add_argument("--design", default="auto",
                     choices=["auto", "full", "fractional", "pb"])
    gen.add_argument("--seed", type=int, default=0)
    gen.set_defaults(func=cmd_generate)

    ana = sub.add_parser("analyze", help="fit OLS effects from measured results")
    ana.add_argument("--design", required=True, help="path to design JSON from generate")
    ana.add_argument("--results", required=True,
                     help="path to JSONL with {run_id, value} or {run_id, values:{...}} per line")
    ana.add_argument("--direction", default="lower", choices=["lower", "higher"],
                     help="for single-metric path only")
    ana.add_argument(
        "--objectives",
        default=None,
        help=(
            "JSON list of objectives or path to file shaped "
            '[{"name","direction","weight"}] or {"objectives":[...],"selection":"..."}'
        ),
    )
    ana.add_argument(
        "--selection",
        default=None,
        choices=["scalarize", "desirability", "pareto"],
        help="override selection method (default: scalarize, or from --objectives file)",
    )
    ana.add_argument("--min-effect", type=float, default=None, dest="min_effect",
                     help="practical-significance threshold in raw units "
                          "(single-metric path; multi-objective reads it per objective)")
    ana.set_defaults(func=cmd_analyze)

    conf = sub.add_parser("confirm", help="judge confirmation runs at the best run and "
                                          "apply the done criteria")
    conf.add_argument("--design", required=True)
    conf.add_argument("--results", required=True, help="the design's results JSONL")
    conf.add_argument("--confirmation", required=True,
                      help='JSONL of confirmation runs at best_factors: {"values": {...}}')
    conf.add_argument("--objectives", default=None)
    conf.add_argument("--contract", default=None,
                      help="promotion contract JSON or file; omitted means descriptive confirmation only")
    conf.add_argument("--selection", default=None,
                      choices=["scalarize", "desirability", "pareto"])
    conf.add_argument("--direction", default="lower", choices=["lower", "higher"],
                      help="single-metric path only")
    conf.add_argument("--target", type=float, default=None, help="single-metric: absolute goal")
    conf.add_argument("--baseline", type=float, default=None, help="single-metric: pre-experiment value")
    conf.add_argument("--min-effect", type=float, default=None, dest="min_effect",
                      help="single-metric: smallest change worth shipping")
    conf.add_argument("--alpha", type=float, default=0.05)
    conf.set_defaults(func=cmd_confirm)

    det = sub.add_parser("detect", help="show which design auto-selects for k factors")
    det.add_argument("factor_count")
    det.set_defaults(func=cmd_detect)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        sys.stderr.write(f"input error: {exc}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
