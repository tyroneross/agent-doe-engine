---
description: Generate or analyze a DOE matrix directly — test many input variables in few runs.
argument-hint: "generate|analyze ..."
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

Direct entry to the DOE engine for: $ARGUMENTS

The point of DOE is fewer runs: 2–3 factors → ≤8 runs, 4–7 → 8 runs, 8–11 → 12-run screening — far fewer than changing one variable at a time.

**Which design for k factors:**
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/doe.py detect <k>
```

**Generate a matrix:**
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/doe.py generate \
  --factors "<json-or-path>" --design auto --seed 1
```

**Analyze measured results** (single-metric):
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/doe.py analyze \
  --design .multi-goal/optimize/doe.json \
  --results .multi-goal/optimize/results.jsonl \
  --direction lower
```

**Analyze for multiple objectives** (add `--objectives`):
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/doe.py analyze \
  --design .multi-goal/optimize/doe.json \
  --results .multi-goal/optimize/results.jsonl \
  --objectives .multi-goal/optimize/objectives.json
```

For the full guided flow (factor identification, measurement, selection, review), use `/multi-goal` instead. Map any supplied arguments to the closest invocation and run it from the target repo.
