# agent-doe-engine

A design-of-experiments engine for tuning AI agents.

It runs a small, structured set of trials that change several settings at once, then measures which settings actually move each metric you care about (speed, cost, quality, accuracy) and which combination best balances goals that compete. It separates real effects from flukes, and flags when two settings are tangled so you can't credit one over the other. Built for agent tuning: which model, which prompt, which setup, answered in a few runs instead of guessing.

> Extracted and extended from [build-loop](https://github.com/tyroneross/build-loop)'s single-metric `optimize` subsystem. agent-doe-engine adds true multi-objective selection (scalarization, Derringer-Suich desirability, Pareto frontier) while keeping the same numpy-only DOE engine.

## Why

Most "make it faster" work optimizes one number and silently regresses another. agent-doe-engine measures every objective on every experimental run, fits which factors move which number, and selects the run that best satisfies all goals under explicit weights. No vibes, no single-metric tunnel vision.

## Two modes

- **DOE mode (default).** Test up to 11 factors in a single experiment. 2 to 3 factors give a full factorial; 4 to 7 give a fractional factorial (8 runs); 8 to 11 give a Plackett-Burman screening (12 runs). Fits main effects plus interactions per objective.
- **Autoresearch mode (fallback).** One factor, greedy loop: hypothesize, measure, keep if better else revert.

## Selection methods

| Method | What it does | Use when |
|---|---|---|
| `scalarize` | Best weighted sum of normalized objectives | You can express priorities as weights |
| `desirability` | Derringer-Suich D (geometric mean of per-objective desirabilities) | You want each objective to clear a bar, not just average out |
| `pareto` | The non-dominated trade-off set | You want to see all the trade-offs before committing |

## Install

**As a Claude Code plugin** (this repo is its own single-plugin marketplace):
```text
/plugin marketplace add tyroneross/agent-doe-engine
/plugin install agent-doe-engine@agent-doe-engine
```
Then `/agent-doe` (guided flow), `/doe` (direct matrix), and `/status` are available. The host coding agent's LLM does the reasoning (hypotheses, factor confirmation); the scripts are deterministic and host-neutral.

**As a Codex plugin** (a `.codex-plugin/plugin.json` manifest ships alongside the Claude one; point Codex at the repo).

**Standalone** (the scripts run on their own):
```bash
uv run python scripts/doe.py detect 4          # which design for 4 factors
uv run pytest -q                                # test suite
```
Requirements: Python >=3.10, numpy. Dev: pytest.

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
| `scripts/doe.py` | DOE matrix generation plus multi-response effects analysis |
| `scripts/objectives.py` | multi-objective core: scalarize, desirability, Pareto, baseline aggregate |
| `scripts/loop.py` | single/few-variable autoresearch greedy loop |
| `scripts/suggest_factors.py` | codebase scanner for factor candidates |
| `scripts/metric_runner.py` | sampled metric / guard execution |

## License

Apache-2.0 (c) Tyrone Ross, Jr. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
