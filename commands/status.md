---
description: Show the current agent-doe-engine optimization experiment summary.
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

Resolve `DOE_ENGINE_ROOT` to the absolute plugin/repository root before running these commands (see `docs/hosts.md`). For an installed Python package, use the corresponding `agent-doe-engine` subcommand.

Run:

```bash
python3 "${DOE_ENGINE_ROOT}/scripts/loop.py" --summary --workdir "$PWD"
```

The output is JSON. If `active` is `false`, tell the user there is no active experiment (no `.agent-doe-engine/optimize/experiment.json`). Otherwise report the target, iterations (kept / discarded / errors), baseline vs current best, improvement %, and the top changes.
