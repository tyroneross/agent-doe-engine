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

**As a Claude Code plugin** — this repo is its own single-plugin marketplace:
```text
/plugin marketplace add tyroneross/multi-goal
/plugin install multi-goal@multi-goal
```
Then `/multi-goal` (guided flow), `/doe` (direct matrix), and `/status` are available. The host coding agent's LLM does the reasoning (hypotheses, factor confirmation); the scripts are deterministic and host-neutral.

**As a Codex plugin** — a `.codex-plugin/plugin.json` manifest ships alongside the Claude one; point Codex at the repo.

**Standalone** — the scripts run on their own:
```bash
uv run python scripts/doe.py detect 4          # which design for 4 factors
uv run pytest -q                                # 76 tests
```
Requirements: Python ≥3.10, numpy. Dev: pytest.

## Quick start

```bash
# 1. which design for k factors
python3 scripts/doe.py detect 2
# 2. generate the matrix
python3 scripts/doe.py generate --factors '[{"name":"workers","low":2,"high":8},{"name":"batch","low":16,"high":64}]' --design auto --seed 1 > doe.json
# 3. run each row, measure every objective into results.jsonl, then:
python3 scripts/doe.py analyze --design doe.json --results results.jsonl \
  --objectives '{"objectives":[{"name":"latency","direction":"lower","weight":0.7},{"name":"cost","direction":"lower","weight":0.3}],"selection":"scalarize"}'
```

Full walkthrough and the method/math: [`docs/usage.md`](docs/usage.md), [`docs/method.md`](docs/method.md).

## Scripts

| Script | Role |
|---|---|
| `scripts/doe.py` | DOE matrix generation + multi-response effects analysis |
| `scripts/objectives.py` | multi-objective core: scalarize, desirability, Pareto, baseline aggregate |
| `scripts/loop.py` | single/few-variable autoresearch greedy loop |
| `scripts/suggest_factors.py` | codebase scanner for factor candidates |
| `scripts/metric_runner.py` | sampled metric / guard execution |

## License

Apache-2.0 © Tyrone Ross, Jr. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
