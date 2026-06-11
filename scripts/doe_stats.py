#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Hand-rolled statistical inference for DOE effects — stdlib + numpy only.

multi-goal is deliberately numpy-only (no scipy / statsmodels). This module
implements, by hand:

  - the regularized incomplete beta function I_x(a, b) via the Lentz
    continued-fraction method (Numerical Recipes betacf/betai),
  - the Student-t two-sided survival probability (p-value) and CDF,
  - the Student-t critical value (inverse CDF) by bisection, for CIs,
  - pooled within-cell "pure error" variance for replicated designs,
  - per-coefficient standard errors from the (XᵀX)⁻¹ diagonal.

Every routine is verified against published reference values in
tests/test_doe_stats.py (e.g. t=2.0, df=8 → two-sided p ≈ 0.0805).

Why the incomplete beta route: the Student-t CDF has the closed form

    P(T ≤ t) = 1 - 0.5 * I_{x}(df/2, 1/2),  x = df / (df + t²)   for t ≥ 0

and the two-sided p-value for a t-statistic with `df` degrees of freedom is

    p = I_{x}(df/2, 1/2),                     x = df / (df + t²)

which needs only `math.lgamma` + a continued fraction — no scipy.
"""

from __future__ import annotations

import math

import numpy as np

# ---------------------------------------------------------------------------
# Regularized incomplete beta function I_x(a, b)
# ---------------------------------------------------------------------------

_BETACF_MAXIT = 300
_BETACF_EPS = 3.0e-16
_BETACF_FPMIN = 1.0e-300


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the incomplete beta function (Lentz's method).

    Mirrors Numerical Recipes `betacf`. Converges for x < (a+1)/(a+b+2);
    `betai` swaps arguments to stay in the convergent region.
    """
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < _BETACF_FPMIN:
        d = _BETACF_FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, _BETACF_MAXIT + 1):
        m2 = 2 * m
        # even step
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < _BETACF_FPMIN:
            d = _BETACF_FPMIN
        c = 1.0 + aa / c
        if abs(c) < _BETACF_FPMIN:
            c = _BETACF_FPMIN
        d = 1.0 / d
        h *= d * c
        # odd step
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < _BETACF_FPMIN:
            d = _BETACF_FPMIN
        c = 1.0 + aa / c
        if abs(c) < _BETACF_FPMIN:
            c = _BETACF_FPMIN
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < _BETACF_EPS:
            break
    return h


def betai(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta function I_x(a, b), x ∈ [0, 1].

    Verified against known values (e.g. I_0.5(1,1)=0.5, I_x(0.5,0.5) symmetry).
    """
    if x < 0.0 or x > 1.0:
        raise ValueError(f"betai: x must be in [0,1], got {x}")
    if x == 0.0:
        return 0.0
    if x == 1.0:
        return 1.0
    # factor exp(ln Beta-numerator) keeps it numerically stable
    ln_beta = (
        math.lgamma(a + b)
        - math.lgamma(a)
        - math.lgamma(b)
        + a * math.log(x)
        + b * math.log(1.0 - x)
    )
    bt = math.exp(ln_beta)
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


# ---------------------------------------------------------------------------
# Student-t distribution (CDF, two-sided p-value, critical value)
# ---------------------------------------------------------------------------

def t_sf_two_sided(t: float, df: float) -> float:
    """Two-sided Student-t survival probability: P(|T| ≥ |t|).

    This is the p-value for a t-statistic `t` with `df` degrees of freedom.

        p = I_{x}(df/2, 1/2),  x = df / (df + t²)

    Reference: t=2.0, df=8 → p ≈ 0.0805; t=0 → p = 1.0.
    df ≤ 0 has no error degrees of freedom; p is undefined → return NaN.
    """
    if df <= 0:
        return float("nan")
    if not math.isfinite(t):
        return 0.0
    x = df / (df + t * t)
    return betai(df / 2.0, 0.5, x)


def t_cdf(t: float, df: float) -> float:
    """Student-t cumulative distribution P(T ≤ t).

    Built from the two-sided tail: for t ≥ 0, P(T ≤ t) = 1 - p/2;
    by symmetry P(T ≤ -t) = p/2.
    """
    if df <= 0:
        return float("nan")
    p = t_sf_two_sided(t, df)
    if t >= 0:
        return 1.0 - p / 2.0
    return p / 2.0


def t_ppf(q: float, df: float) -> float:
    """Inverse Student-t CDF (quantile / critical value) by bisection.

    Returns t such that P(T ≤ t) = q. Used for two-sided CIs:
    the 95% critical value is t_ppf(0.975, df). Bisection is robust and
    needs no derivative; the CDF is monotone so it always converges.
    """
    if df <= 0:
        return float("nan")
    if q <= 0.0:
        return float("-inf")
    if q >= 1.0:
        return float("inf")
    if q == 0.5:
        return 0.0
    # Bracket: symmetric, widen until it contains q.
    lo, hi = -1.0, 1.0
    while t_cdf(lo, df) > q:
        lo *= 2.0
        if lo < -1e9:
            break
    while t_cdf(hi, df) < q:
        hi *= 2.0
        if hi > 1e9:
            break
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        c = t_cdf(mid, df)
        if abs(c - q) < 1e-12:
            return mid
        if c < q:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


# ---------------------------------------------------------------------------
# Pooled within-cell "pure error" variance (replicated designs)
# ---------------------------------------------------------------------------

def pooled_pure_error(cell_values: list[list[float]]) -> tuple[float, int]:
    """Pooled within-cell variance ("pure error") and its degrees of freedom.

    cell_values[i] = the list of replicate measurements observed at design
    cell i. Cells with a single observation contribute no within-cell SS and
    zero df (n_i - 1 = 0).

        pooled_var = Σ_i Σ_r (y_ir - ȳ_i)²  /  Σ_i (n_i - 1)
        pooled_df  = Σ_i (n_i - 1)

    Returns (pooled_variance, pooled_df). When no cell is replicated the SS is
    0 and df is 0 → caller must fall back to the OLS residual error term.

    This is the statistically correct error term for a stochastic response:
    it is independent of the fitted model and measures only measurement /
    process noise (Montgomery, Design and Analysis of Experiments, §6.4).
    """
    total_ss = 0.0
    total_df = 0
    for reps in cell_values:
        n_i = len(reps)
        if n_i < 2:
            continue
        arr = np.asarray(reps, dtype=float)
        mean = arr.mean()
        total_ss += float(np.sum((arr - mean) ** 2))
        total_df += n_i - 1
    if total_df == 0:
        return 0.0, 0
    return total_ss / total_df, total_df


# ---------------------------------------------------------------------------
# Standard errors from (XᵀX)⁻¹
# ---------------------------------------------------------------------------

def coef_standard_errors(X: np.ndarray, error_var: float) -> np.ndarray:
    """Per-coefficient standard errors: SE_j = sqrt(error_var · (XᵀX)⁻¹_jj).

    For an orthogonal coded design every (XᵀX)⁻¹ diagonal entry is 1/n, so
    SE is constant across coefficients — but we compute the full inverse so
    non-orthogonal / truncated designs stay correct.
    """
    xtx = X.T @ X
    try:
        xtx_inv = np.linalg.inv(xtx)
    except np.linalg.LinAlgError:
        xtx_inv = np.linalg.pinv(xtx)
    diag = np.clip(np.diag(xtx_inv), 0.0, None)
    return np.sqrt(error_var * diag)
