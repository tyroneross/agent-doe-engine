#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""DOE matrix generation + effects analysis for multi-goal:optimize.

Stdlib + numpy only. Provably equivalent to pyDOE3 1.6.2 for the three
designs we care about (full factorial, fractional factorial, Plackett-Burman
12-run) — verified by side-by-side comparison with off-diag(XᵀX)=0 and
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
# Design generators (mirrors pyDOE3 — see tests/test_doe.py)
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
    sign — that is the operational definition of confounding, independent of
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
    # orthogonal yet no exact ±1 alias group / identity word exists — its
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
            "defining_relation": ["I (non-regular — no clean defining relation)"],
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
                "relation recovered — interpret confounded terms together.")

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


def _inference_verdict(error_df: int) -> tuple[str, list[str]]:
    """Map error degrees of freedom → a plain-language trust verdict + warnings.

    The verdict tells consumers whether the p-values are estimates they can
    trust, directional-only, or absent (exact fit). It is the headline trust
    signal that stops over-reading effect magnitude on a saturated design.
    """
    warnings: list[str] = []
    if error_df <= 0:
        verdict = "saturated — no error df; effects are exact fits, not estimates"
        warnings.append(
            "Saturated design: 0 error degrees of freedom. Standard errors, "
            "t-statistics, and p-values cannot be computed. Effect magnitudes "
            "are exact fits to the data, not statistical estimates — add "
            "replicates or drop terms to obtain an error estimate."
        )
    elif error_df <= 3:
        verdict = f"low power — only {error_df} error df; directional only"
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
    beta, residuals, rank, _ = np.linalg.lstsq(X, y, rcond=None)

    intercept = float(beta[0])
    main_effects = {labels[i][1]: float(beta[i]) for i in range(1, len(labels))
                    if labels[i][0] == "main"}
    inter_effects = {labels[i][1]: float(beta[i]) for i in range(1, len(labels))
                     if labels[i][0] == "inter"}

    # ---- Error term selection -------------------------------------------
    # Residual term from the model fit (always available, may have 0 df).
    fitted = X @ beta
    residual_ss = float(np.sum((y - fitted) ** 2))
    residual_df = int(n - rank)

    pure_error_var, pure_error_df = (0.0, 0)
    if cell_values is not None:
        pure_error_var, pure_error_df = doe_stats.pooled_pure_error(cell_values)

    if pure_error_df > 0:
        # Replicated design: pure error is the correct denominator.
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

    # ---- Variance explained (r²) ----------------------------------------
    y_var = float(np.var(y) * n)
    if residual_df >= 0 and y_var > 0 and rank == p_terms:
        r2 = 1.0 - residual_ss / y_var
    elif y_var <= 0:
        r2 = 1.0
    else:
        r2 = None

    # ---- Per-coefficient inference --------------------------------------
    verdict, warnings = _inference_verdict(error_df)
    if error_df <= 0 and pure_error_df == 0 and cell_values is not None:
        warnings.append(
            "No replicated cells found: error estimate falls back to OLS "
            "residual. For a stochastic/LLM response, add replicate runs so "
            "significance rests on measured pure error, not model residual."
        )

    if error_df > 0:
        ses = doe_stats.coef_standard_errors(X, error_var)
        t_crit = doe_stats.t_ppf(0.975, error_df)
    else:
        ses = np.full(p_terms, float("nan"))
        t_crit = float("nan")

    def _stat(idx: int) -> dict:
        coef = float(beta[idx])
        se = float(ses[idx])
        if error_df > 0 and se > 0:
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


def rank_findings(effects: dict, factor_names: list[str]) -> list[dict]:
    """Sort effects by absolute magnitude with human-readable labels.

    Each row now carries the per-effect trust signals (se, t, p_value, ci95,
    significant) so a consumer ranking by magnitude can still see whether the
    top effect is statistically distinguishable from noise.
    """
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
        rows.append(row)
    for (i, j), val in effects["interactions"].items():
        row = {
            "term": f"{factor_names[i]} × {factor_names[j]}",
            "kind": "interaction",
            "effect": val,
            "abs_effect": abs(val),
        }
        row.update(inter_stats.get((i, j), {}))
        rows.append(row)
    rows.sort(key=lambda r: -r["abs_effect"])
    return rows


# ---------------------------------------------------------------------------
# Level mapping (-1/+1 coding ↔ user-specified levels)
# ---------------------------------------------------------------------------

def map_levels(design: np.ndarray, factors: list[dict]) -> list[dict]:
    """Convert ±1 coded design into named runs with concrete values.
    factors[i] = {"name": str, "low": <value>, "high": <value>} OR
    factors[i] = {"name": str, "levels": [<low>, <high>]}."""
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
                value = float(coded)  # fallback to coded value
            run["_factors"][f["name"]] = value
        runs.append(run)
    return runs


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_generate(args: argparse.Namespace) -> int:
    factors = json.loads(Path(args.factors).read_text()) if Path(args.factors).is_file() \
        else json.loads(args.factors)
    if not isinstance(factors, list) or not factors:
        sys.stderr.write("--factors must be a JSON list of {name, low, high} or {name, levels}\n")
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
        "factors": [{"name": f["name"]} for f in factors],
        "matrix": matrix.tolist(),
        "run_order": order,
        "runs": runs,
        "aliasing": aliasing,
    }
    print(json.dumps(output, indent=2))
    return 0


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

    raw = arg_objectives
    p = Path(raw)
    if p.is_file():
        raw = p.read_text()

    parsed = json.loads(raw)

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


def cmd_analyze(args: argparse.Namespace) -> int:
    design_data = json.loads(Path(args.design).read_text())
    matrix = np.array(design_data["matrix"], dtype=float)
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

    if obj_list is None:
        # ---- Backward-compatible single-metric path (UNCHANGED) --------
        results: dict[int, float] = {}
        for line in Path(args.results).read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            results[int(row["run_id"])] = float(row["value"])
        if len(results) != n:
            sys.stderr.write(f"need {n} results, got {len(results)}\n")
            return 2
        y = np.array([results[i] for i in range(n)])
        effects = fit_effects(matrix, y, include_interactions=include_interactions)
        findings = rank_findings(effects, factor_names)
        direction = args.direction or "lower"
        best_run_idx = int(np.argmin(y)) if direction == "lower" else int(np.argmax(y))
        best_factors: dict | None = None
        runs_block = design_data.get("runs")
        if isinstance(runs_block, list) and 0 <= best_run_idx < len(runs_block):
            candidate = runs_block[best_run_idx].get("_factors")
            if isinstance(candidate, dict):
                best_factors = candidate
        output = {
            "summary": {
                "design_type": design_data["design"]["type"],
                "n_runs": n,
                "n_factors": k,
                "r2": effects["r2"],
                "intercept": effects["intercept"],
            },
            "ranked_effects": findings,
            "best_run": best_run_idx,
            "best_value": float(np.min(y)) if direction == "lower" else float(np.max(y)),
            "direction": direction,
        }
        if best_factors is not None:
            output["best_factors"] = best_factors
        print(json.dumps(output, indent=2))
        return 0

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
            row = {"run_id": row["run_id"], "values": {single_obj_name: row["value"]},
                   "guard_ok": row.get("guard_ok", True)}
        raw_results.append(row)

    if len(raw_results) != n:
        sys.stderr.write(f"need {n} results, got {len(raw_results)}\n")
        return 2

    # Build lookup: run_id -> row
    results_by_id: dict[int, dict] = {int(r["run_id"]): r for r in raw_results}

    # Per-objective effects analysis
    per_objective: dict[str, dict] = {}
    for obj in obj_list:
        obj_name = obj["name"]
        direction = obj.get("direction", "lower")
        y_obj = np.array([
            float(results_by_id[i]["values"][obj_name]) for i in range(n)
        ])
        eff = fit_effects(matrix, y_obj, include_interactions=include_interactions)
        findings = rank_findings(eff, factor_names)
        per_objective[obj_name] = {
            "ranked_effects": findings,
            "r2": eff["r2"],
            "intercept": eff["intercept"],
            "direction": direction,
        }

    # Build runs list for objectives.select_best
    runs_for_selection = [
        {"run_id": int(results_by_id[i]["run_id"]),
         "values": {k_: float(v) for k_, v in results_by_id[i]["values"].items()}}
        for i in range(n)
    ]

    try:
        sel_result = objectives.select_best(runs_for_selection, obj_list, selection)
    except Exception as exc:
        sys.stderr.write(f"objectives.select_best error: {exc}\n")
        return 2

    best_run_id = sel_result["best_run_id"]

    # Pull concrete factor levels for best run
    best_factors_multi: dict | None = None
    runs_block = design_data.get("runs")
    if isinstance(runs_block, list) and 0 <= best_run_id < len(runs_block):
        candidate = runs_block[best_run_id].get("_factors")
        if isinstance(candidate, dict):
            best_factors_multi = candidate

    output = {
        "summary": {
            "design_type": design_data["design"]["type"],
            "n_runs": n,
            "n_factors": k,
            "selection": selection,
        },
        "per_objective": per_objective,
        "selection": sel_result,
        "best_run": best_run_id,
    }
    if best_factors_multi is not None:
        output["best_factors"] = best_factors_multi

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
    ana.set_defaults(func=cmd_analyze)

    det = sub.add_parser("detect", help="show which design auto-selects for k factors")
    det.add_argument("factor_count")
    det.set_defaults(func=cmd_detect)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
