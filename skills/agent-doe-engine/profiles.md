<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Optimization Profiles

Each profile is a single-objective preset. Compose several into one `objectives.json` to optimize them together (see the multi-objective example at the bottom).

## simplify (always available)

Reduce code complexity in files touched recently.

- **Metric**: `wc -l <scope_files> | tail -1 | awk '{print $1}'` (total lines)
- **Guard**: `npm run build` or `npm test` (must still compile/pass)
- **Direction**: lower
- **Scope**: changed files (`git diff --name-only HEAD~N`)
- **Finds**: dead imports, unused variables, redundant files, inlinable one-use helpers

## build-time

- **Metric**: `/usr/bin/time -p npm run build 2>&1 | grep ^real | awk '{print $2}'`
- **Guard**: `npm test -- --passWithNoTests`
- **Direction**: lower
- **Scope**: build/bundler/tsconfig

## coverage

- **Metric**: coverage % from the test runner
- **Guard**: all existing tests pass
- **Direction**: higher
- **Scope**: test files only

## bundle-size

- **Metric**: `du -sk dist 2>/dev/null | awk '{print $1}'` (KB)
- **Guard**: `npm run build`
- **Direction**: lower
- **Scope**: source files importing large dependencies

## latency

Use when a workload has a benchmark command but no specialized preset.

- **Metric**: a benchmark command that prints one number
- **Guard**: test suite passes
- **Direction**: lower
- **Benchmark guidance**: prefer representative workloads over microbenchmarks. For latency, run repeated samples (`--samples 5-9`), discard cold-start noise (`--warmups 1-2`), aggregate with `median` or `p95`. Re-measure on a fresh process to separate first-run download/JIT from steady-state cold start.

## Multi-objective example - speed without bloat

The whole point of agent-doe-engine: optimize competing profiles together. Write to `.agent-doe-engine/optimize/objectives.json`:

```json
{
  "objectives": [
    {"name": "latency_p95_ms", "direction": "lower",  "weight": 0.5, "metric_cmd": "python3 bench.py --stat p95"},
    {"name": "bundle_kb",      "direction": "lower",  "weight": 0.3, "metric_cmd": "du -sk dist | awk '{print $1}'"},
    {"name": "coverage_pct",   "direction": "higher", "weight": 0.2, "metric_cmd": "pytest --cov | grep -o '[0-9]*%' | tr -d %"}
  ],
  "selection": "desirability"
}
```

`desirability` here means a config that wins on latency but drops coverage to zero is rejected (D=0), even if its weighted average looks fine. Use `scalarize` if you only care about the weighted blend, `pareto` to see every trade-off before deciding.
