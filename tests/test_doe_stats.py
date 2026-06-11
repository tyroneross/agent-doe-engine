#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Tests for doe_stats.py — hand-rolled inference verified against references.

The whole point of this module is that the statistics are CORRECT without
scipy. Every reference value below is independently published (or hand-derived)
so a wrong implementation fails loudly.

Reference sources:
  - Two-sided Student-t p-values: standard t-tables / R `2*pt(-abs(t), df)`.
  - Incomplete beta identities: I_x(a,b) = 1 - I_{1-x}(b,a); I_0.5(1,1)=0.5.
  - Pooled pure error: Montgomery DAE §6.4, hand-computed below.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
import doe_stats as ds  # noqa: E402


# ---------------------------------------------------------------------------
# Incomplete beta function
# ---------------------------------------------------------------------------

def test_betai_endpoints():
    assert ds.betai(0.5, 0.5, 0.0) == 0.0
    assert ds.betai(0.5, 0.5, 1.0) == 1.0


def test_betai_uniform_case():
    # a=b=1 → Beta(1,1) is Uniform(0,1) → I_x(1,1) = x
    for x in (0.1, 0.25, 0.5, 0.73, 0.99):
        assert math.isclose(ds.betai(1.0, 1.0, x), x, abs_tol=1e-12)


def test_betai_symmetry_identity():
    # I_x(a,b) = 1 - I_{1-x}(b,a)
    for a, b, x in [(2.0, 3.0, 0.4), (0.5, 0.5, 0.3), (4.0, 1.5, 0.8)]:
        lhs = ds.betai(a, b, x)
        rhs = 1.0 - ds.betai(b, a, 1.0 - x)
        assert math.isclose(lhs, rhs, abs_tol=1e-12), (a, b, x, lhs, rhs)


def test_betai_symmetric_half():
    # By symmetry I_0.5(c,c) = 0.5 for any c
    for c in (0.5, 1.0, 2.5, 10.0):
        assert math.isclose(ds.betai(c, c, 0.5), 0.5, abs_tol=1e-12)


# ---------------------------------------------------------------------------
# Student-t two-sided p-value (the headline reference: t=2.0, df=8 → 0.0805)
# ---------------------------------------------------------------------------

def test_t_pvalue_reference_t2_df8():
    # Exact: I_x(4, 0.5) with x = 8/(8+4) = 2/3, cross-checked to 30 dp via
    # mpmath.betainc → 0.08051623795726267 (R's 2*pt(-2,8) rounds this to 0.0805).
    p = ds.t_sf_two_sided(2.0, 8)
    assert math.isclose(p, 0.080516238, abs_tol=1e-8), p


def test_t_pvalue_known_table_values():
    # Published two-sided p-values (R 2*pt(-abs(t), df)).
    cases = [
        (2.306, 8, 0.05),     # t_0.975,8 ≈ 2.306 → p ≈ 0.05
        (3.355, 8, 0.01),     # t_0.995,8 ≈ 3.355 → p ≈ 0.01
        (1.0, 10, 0.34089),   # 2*pt(-1,10)
        (2.228, 10, 0.05),    # t_0.975,10 ≈ 2.228
        (12.706, 1, 0.05),    # t_0.975,1 ≈ 12.706 (Cauchy)
    ]
    for t, df, expected in cases:
        p = ds.t_sf_two_sided(t, df)
        assert math.isclose(p, expected, abs_tol=2e-4), (t, df, p, expected)


def test_t_pvalue_t_zero_is_one():
    assert math.isclose(ds.t_sf_two_sided(0.0, 5), 1.0, abs_tol=1e-12)


def test_t_pvalue_sign_symmetric():
    for df in (1, 3, 8, 30):
        assert math.isclose(
            ds.t_sf_two_sided(2.5, df), ds.t_sf_two_sided(-2.5, df), abs_tol=1e-14
        )


def test_t_pvalue_zero_df_is_nan():
    assert math.isnan(ds.t_sf_two_sided(2.0, 0))


# ---------------------------------------------------------------------------
# Student-t CDF and inverse
# ---------------------------------------------------------------------------

def test_t_cdf_median():
    for df in (1, 5, 20):
        assert math.isclose(ds.t_cdf(0.0, df), 0.5, abs_tol=1e-12)


def test_t_cdf_complement():
    for df in (3, 8, 25):
        assert math.isclose(ds.t_cdf(1.7, df) + ds.t_cdf(-1.7, df), 1.0, abs_tol=1e-12)


def test_t_ppf_reference_critical_values():
    # Standard 97.5% two-sided critical values.
    cases = [(8, 2.306004), (10, 2.228139), (1, 12.706205), (30, 2.042272)]
    for df, expected in cases:
        crit = ds.t_ppf(0.975, df)
        assert math.isclose(crit, expected, abs_tol=1e-4), (df, crit, expected)


def test_t_ppf_round_trip():
    for df in (2, 8, 15):
        for q in (0.6, 0.9, 0.975, 0.995):
            t = ds.t_ppf(q, df)
            assert math.isclose(ds.t_cdf(t, df), q, abs_tol=1e-6), (df, q, t)


# ---------------------------------------------------------------------------
# Pooled pure error
# ---------------------------------------------------------------------------

def test_pooled_pure_error_hand_example():
    # Two cells, each with replicates:
    #   cell A: [10, 12, 14] → mean 12, SS = 4+0+4 = 8,  df = 2
    #   cell B: [20, 24]     → mean 22, SS = 4+4   = 8,  df = 1
    # pooled SS = 16, pooled df = 3 → pooled_var = 16/3
    var, df = ds.pooled_pure_error([[10, 12, 14], [20, 24]])
    assert df == 3
    assert math.isclose(var, 16.0 / 3.0, abs_tol=1e-12)


def test_pooled_pure_error_single_obs_cells_zero():
    # No cell has ≥2 obs → no pure error, df 0
    var, df = ds.pooled_pure_error([[5.0], [7.0], [9.0]])
    assert df == 0
    assert var == 0.0


def test_pooled_pure_error_mixed():
    # cell A replicated, cells B/C single → only A contributes
    #   A: [1, 3] → mean 2, SS = 1+1 = 2, df 1
    var, df = ds.pooled_pure_error([[1.0, 3.0], [8.0], [9.0]])
    assert df == 1
    assert math.isclose(var, 2.0, abs_tol=1e-12)


def test_pooled_variance_equals_known_variance():
    # A single cell's pooled "variance" is just the sample variance (ddof=1).
    reps = [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]
    var, df = ds.pooled_pure_error([reps])
    assert df == len(reps) - 1
    assert math.isclose(var, float(np.var(reps, ddof=1)), abs_tol=1e-12)


# ---------------------------------------------------------------------------
# Standard errors from (XᵀX)⁻¹
# ---------------------------------------------------------------------------

def test_coef_se_orthogonal_design():
    # 2^2 full factorial coded model [intercept, x1, x2]: XᵀX = 4·I,
    # so (XᵀX)⁻¹ = 0.25·I and SE_j = sqrt(error_var · 0.25).
    d = np.array([[-1, -1], [-1, 1], [1, -1], [1, 1]], dtype=float)
    X = np.column_stack([np.ones(4), d[:, 0], d[:, 1]])
    error_var = 4.0
    se = ds.coef_standard_errors(X, error_var)
    expected = math.sqrt(4.0 * 0.25)  # = 1.0
    assert np.allclose(se, expected), se


def test_coef_se_scales_with_error_var():
    d = np.array([[-1, -1], [-1, 1], [1, -1], [1, 1]], dtype=float)
    X = np.column_stack([np.ones(4), d[:, 0], d[:, 1]])
    se1 = ds.coef_standard_errors(X, 1.0)
    se4 = ds.coef_standard_errors(X, 4.0)
    assert np.allclose(se4, 2.0 * se1)  # SE ∝ sqrt(var)
