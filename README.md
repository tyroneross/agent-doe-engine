# multi-goal

**Multi-objective optimization via Design of Experiments.** Optimize several competing metrics at once — latency *and* cost *and* bundle size *and* coverage — and find the factor settings that best trade them off. A focused, standalone, host-agnostic plugin.

> Extracted and extended from [build-loop](https://github.com/tyroneross/build-loop)'s single-metric `optimize` subsystem. multi-goal adds true multi-objective selection (scalarization, Derringer-Suich desirability, Pareto frontier) while keeping the same numpy-only DOE engine.

## Why

Most "make it faster" work optimizes one number and silently regresses another. multi-goal measures every objective on every experimental run, fits which factors move which number, and selects the run that best satisfies all goals under explicit weights — no vibes, no single-metric tunnel vision.

## Two modes

- **DOE mode (default).** Test up to 11 factors in a single experiment. 2–3 factors → full factorial; 4–7 → fractional factorial (8 runs); 8–11 → Plackett-Burman screening (12 runs). Fits main effects + interactions per objective.
- **Autoresearch mode (fallback).** One factor, greedy loop: hypothesize → measure → keep-if-better-else-revert.

## Selection methods

| Method | What it does | Use when |
|---|---|---|
| `scalarize` | Best weighted sum of normalized objectives | You can express priorities as weights |
| `desirability` | Derringer-Suich D (geometric mean of per-objective desirabilities) | You want each objective to clear a bar, not just average out |
| `pareto` | The non-dominated trade-off set | You want to see all the trade-offs before committing |

## Install

```bash
# as a Claude Code plugin (via marketplace or local path)
# scripts run standalone too:
uv run python scripts/doe.py detect 4
```

## Quick start

See [`docs/`](docs/) for the full walkthrough and method notes. Requirements: Python ≥3.10, numpy.

## License

Apache-2.0 © Tyrone Ross, Jr. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
