---
description: Show the current multi-goal optimization experiment summary.
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

Run:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/loop.py --summary --workdir "$PWD"
```

The output is JSON. If `active` is `false`, tell the user there is no active experiment (no `.multi-goal/optimize/experiment.json`). Otherwise report the target, iterations (kept / discarded / errors), baseline vs current best, improvement %, and the top changes.
