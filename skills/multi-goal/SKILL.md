---
name: multi-goal
description: Use when the user wants to optimize one or more measurable numbers at once — "optimize this", "make X faster without blowing up Y", "reduce latency and cost", "find the best trade-off between A and B", "tune these parameters", "speed up my app". Runs a Design of Experiments matrix (up to 11 factors in one pass), measures every objective on every run, and selects the best trade-off by weighted scalarization, Derringer-Suich desirability, or Pareto frontier. Falls back to a single-variable autoresearch loop.
user-invocable: true
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- multi-goal@tyroneross:canary:multi-goal -->
<!-- canary-end -->

# multi-goal

Optimize numbers you can measure — and when several numbers compete, find the setting that best trades them off. Build time, latency, token cost, bundle size, coverage, accuracy. Anything where a one-line command returns a number.

The metric is the only judge. No "this looks better."

`${CLAUDE_PLUGIN_ROOT}` below is the plugin root; from a clone it's the repo root. Runtime state lives in the **consumer** project under `.multi-goal/optimize/`.

## Three shapes of request

| Shape | Trigger | Path |
|---|---|---|
| **Multi-objective** *(the differentiator)* | ≥2 competing numbers ("faster AND cheaper", "latency vs accuracy") | DOE with an `objectives` list + a `selection` method |
| **Single-metric, multi-factor** | one number, several knobs to test | DOE, single objective |
| **Single-metric, single-factor** | one number, one thing to try | autoresearch greedy loop |

## Phase 1: SETUP — get the objectives and factors right

Wrong metric = Goodhart's Law. Wrong factors = wasted runs. This is the highest-leverage phase.

### 1.1 — Name the objectives

Each objective is a number plus how to read it:

```json
{
  "objectives": [
    {"name": "latency_ms",   "direction": "lower",  "weight": 0.5, "metric_cmd": "python3 bench.py --stat p95"},
    {"name": "cost_usd",     "direction": "lower",  "weight": 0.3, "metric_cmd": "python3 cost.py"},
    {"name": "coverage_pct", "direction": "higher", "weight": 0.2, "metric_cmd": "pytest --cov | tail -1 | grep -o '[0-9]*%'"}
  ],
  "selection": "scalarize"
}
```

Write it to `.multi-goal/optimize/objectives.json`. One objective is the single-metric case — everything below still works.

**Choosing `selection`:**

| Method | Picks | Use when |
|---|---|---|
| `scalarize` *(default)* | max weighted sum of normalized objectives | you can express priorities as weights |
| `desirability` | max Derringer-Suich D (geometric mean of per-objective desirabilities) | every objective must clear a bar — a zero on one tanks the run |
| `pareto` | the non-dominated trade-off set (single winner = max-desirability point on the front) | you want to see all trade-offs before committing |

### 1.2 — Identify factors

If the user named factors ("optimize workers, batch_size, timeout"), validate the shape `[{name, low, high}]` or `[{name, levels:[...]}]` and skip ahead.

Otherwise scan the codebase for candidates:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/suggest_factors.py --workdir "$PWD" --top 12 --json
```
Returns ranked numeric knobs (UPPER_SNAKE constants near tuning keywords, env vars with numeric defaults) with suggested low/center/high levels. **Confirm with the user before running** (AskUserQuestion, candidates pre-checked) — the scanner has false positives (port numbers, toast delays look numeric but aren't perf knobs). Do not auto-run on heuristics.

### 1.3 — Pick the design (≥2 factors)

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/doe.py detect <k>
```
Routing: `k=1` → autoresearch (§Single-factor); `2–3` → 2^k full factorial (≤8 runs); `4–7` → fractional factorial 2^(k-p) Res III/IV (8 runs); `8–11` → Plackett-Burman 12-run screening.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/doe.py generate \
  --factors "$(cat .multi-goal/optimize/factors.json)" \
  --design auto --seed "$RANDOM" \
  > .multi-goal/optimize/doe.json
```

## Phase 2: RUN THE MATRIX

For each row in `.multi-goal/optimize/doe.json` (in randomized `run_order`):

1. Apply the factor values from `runs[i]._factors` to code / config / env.
2. Measure **every objective** — run each objective's `metric_cmd` (use `metric_runner.py` for sampled/aggregated measurement of noisy metrics):
   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/metric_runner.py --cmd "<metric_cmd>" --samples 5 --warmups 1 --aggregate p95
   ```
3. Run the guard (must exit 0): `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/metric_runner.py --guard "<guard_cmd>"`.
4. Append to `.multi-goal/optimize/results.jsonl`: `{"run_id": i, "values": {"latency_ms": .., "cost_usd": ..}, "guard_ok": true}`.
5. Revert the factor changes — each DOE run starts from the same baseline; the design does not accumulate.

Then fit effects and select:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/doe.py analyze \
  --design .multi-goal/optimize/doe.json \
  --results .multi-goal/optimize/results.jsonl \
  --objectives .multi-goal/optimize/objectives.json \
  > .multi-goal/optimize/effects.json
```

Output: ranked main effects + interactions **per objective**, the `selection` result (best run, scores, **always** the `pareto_front`), and `best_factors` (concrete winning values). Apply the winning combination as one commit. If `selection: pareto`, present the front and let the user pick the trade-off; default to the max-desirability point.

## Single-factor — autoresearch loop

When there is one factor (or one thing to try), skip DOE.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/loop.py --init --workdir "$PWD" \
  --target "<name>" --scope "<glob>" \
  --objectives "$(cat .multi-goal/optimize/objectives.json | python3 -c 'import sys,json;print(json.dumps(json.load(sys.stdin)["objectives"]))')" \
  --selection scalarize \
  --metric-cmd "true" --guard-cmd "<cmd>" --budget 20 --direction lower
```

Measure the baseline once, then record it:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/loop.py --set-baseline --workdir "$PWD" \
  --baseline-values '{"latency_ms": 100, "cost_usd": 5}'
```

Then dispatch the `optimize-runner` agent. Each iteration: hypothesize one atomic change → apply → measure every objective → `loop.py --score --values '{...}'` to get the scalar aggregate (improvement ratio vs baseline; >1 = better) → keep if aggregate improves and the guard passes, else `git revert`. Convergence: 5 consecutive discards, regressing trend, or budget exhausted.

Single-objective mode is the original behavior — omit `--objectives` and use `--metric-cmd` directly.

## Phase 3: REVIEW

1. Dispatch `overfitting-reviewer` (read-only): check for removed safety, fragile shortcuts, metric-gaming, scope violations across the kept changes.
2. Summarize: runs, kept/reverted, per-objective improvement, the chosen trade-off.
3. Archive: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/loop.py --archive --workdir "$PWD"`.

## Model tiering (when running under a multi-model host)

| Component | Tier | Why |
|---|---|---|
| Setup (objectives, factors, selection) | Thinking | Wrong metric = Goodhart |
| Hypothesis generation | Code | High volume |
| Metric / guard / analyze | deterministic scripts | no LLM |
| Keep/revert | deterministic | numeric comparison |
| Overfitting review | Code (read-only) | pattern matching |

## State files

```text
.multi-goal/optimize/
├── objectives.json   # objectives + selection method
├── factors.json      # factor definitions
├── doe.json          # generated design matrix
├── results.jsonl     # measured responses per run
├── effects.json      # per-objective effects + selection result
├── experiment.json   # autoresearch config (single/few-factor mode)
├── results.tsv       # autoresearch iteration log
└── experiments/      # archived runs
```

## Profiles

See `profiles.md` for ready-made single-objective presets (simplify, build time, bundle size, latency). Compose them into a multi-objective `objectives.json` when you want to optimize several at once.
