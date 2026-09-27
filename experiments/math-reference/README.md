# Check the mathematics against independent references

Run the engine and base R on matching inputs:

```sh
python3 experiments/math-reference/check.py --output .agent-doe-engine/math-check-UNIQUE
```

Requires `Rscript` and the engine's normal NumPy dependency. It installs nothing and makes no network calls. Results remain in JSON and CSV alongside the R reference table and version receipt. The regular test suite skips the live comparison when R is absent; the published NIST coefficient test still runs.

## Reference coverage

| Calculation | Independent reference | Checks |
| --- | --- | ---: |
| Coded regression coefficients, standard errors, p-values and 95% intervals | NIST eddy-current observations fitted with R `stats::lm` | 35 |
| Paired mean improvement and interval | R `stats::t.test`, paired `sleep` example, group 2 minus group 1 | 3 |
| Student t quantiles | R `stats::qt`, six degrees of freedom and five probabilities | 30 |
| Exact two-sided paired binary test | R `stats::binom.test` on discordant counts | 15 |
| Linear monotone desirability and weighted geometric aggregation | Published equations evaluated independently in base R | 7 |

Tolerance: absolute error at most `1e-10` or relative error at most `1e-8`. The September 27, 2026 run passed all 90 checks with R/stats 4.6.1; maximum absolute difference was about `1.06e-9`. An initial comparison-script error iterated dictionary keys instead of coefficient values; correcting that adapter required no engine calculation change. The original failed receipt is retained separately.

## Sources and interpretation

- [NIST Yates example](https://www.itl.nist.gov/div898/handbook/eda/section3/eda35i.htm) supplies eight observations and published coefficients. The `Estimate` column is a coded regression coefficient; its low-to-high factorial effect is twice that value. The matching R/engine model includes main and two-way terms, leaving only one residual degree of freedom. Matching its arithmetic does not justify strong inference from that small residual estimate.
- [R paired t-test example](https://stat.ethz.ch/R-manual/R-devel/library/stats/html/t.test.html) provides the paired `sleep` data workflow. We reverse the displayed group order to measure group 2 minus group 1 as an improvement.
- [R exact binomial test](https://stat.ethz.ch/R-manual/R-devel/library/stats/html/binom.test.html) supplies the exact conditional reference at probability 0.5. R's [`mcnemar.test`](https://stat.ethz.ch/R-manual/R-devel/library/stats/html/mcnemar.test.html) is a chi-squared approximation and is deliberately not used as an exact-test oracle.
- [CRAN desirability vignette](https://stat.ethz.ch/CRAN/web/packages/desirability/vignettes/desirability.pdf), equations 1–2 and the geometric-mean definition, supplies the linear monotone reference. The optional `desirability` package is not installed; these checks execute its published equations in base R. Target-is-best curves and nonlinear shape parameters are outside this engine's supported comparison.

These are reproducible arithmetic checks, not evidence of correct experimental assumptions, sufficient power, semantic goal alignment, independent tasks or analyst effectiveness. No provider experiment was run for this check.
