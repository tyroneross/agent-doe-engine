# Cursor independent source review

Cursor read-only session completed; shell execution was unavailable to that session. Findings below describe the earlier source snapshot; dispositions are in [review.md](review.md).

I'll review the uncommitted implementation against the plan, focusing on promotion, identity, ledger, stats, Jev, and export safety. Reading the plan and the changed sources first.The plan requires separate numerical confirmation and promotion readiness, plus immutable ledger records and escaped offline reports. I'll inspect those paths in the current sources.The promotion gate and the paired-guard check disagree with the plan’s exclusion rules. I’ll reproduce those paths and re-read the report script before treating the escaping issue as still open.Failed-guard screening replicates can still flip `doe.py confirm` to `recommendation: "ship"`. Three other defects stop short of that, but they still let unguarded or tied evidence produce a positive statistical result. Pytest did not run: the shell was rejected in this read-only session, so the reproductions below are from the current source.

## High

**Failed-guard cells stay in the confirmation error term and can authorize ship.** Selection drops a cell when any replicate has `guard_status` false (`scripts/objectives.py:528-531`). The fit does not. `_collect_multi_metric` keeps every replicate in `cell_values` (`scripts/doe.py:1111-1116`), `fit_effects` uses those replicates for pure-error variance (`scripts/doe.py:400-437`), and `cmd_confirm` judges the winner with that fit (`scripts/doe.py:1574-1577`, `864-866`, `1585-1600`).

For a saturated 2-level design, the prediction at the winning cell stays that cell’s own mean. A different cell with `guard_ok: false` and a huge within-cell spread does not move that prediction; it widens every prediction interval. A confirmation mean that misses a zero-width interval then falls inside the widened one, the point estimate still clears `target`, and a structurally valid contract yields `done: true` and `recommendation: "ship"`.

Reproduction, k=2, two replicates per cell, primary `latency` lower, `target: 100`, three confirmation values of 90 at the guarded winner:

- Winner replicates `80, 80`, `guard_ok: true`. Other three cells tight. Failed cell replicates `0, 200`, `guard_ok: false`.
- Without that failed cell, pure-error variance is 0, the interval is the point 80, and 90 fails `mean_in_pi`. `passed` is forced false. No ship.
- With that cell, pure-error SS is 20000 on 4 df, half-width is on the order of 100, 90 is inside the interval, and ship is allowed.
- The failed cell must not be the selected run. Promotion still requires a matching contract, passing confirmation guards, and matching fixture, scorer, and split on every row.

## Medium

**A failed confirmation row can create `numerical_confirmed: true`.** `_read_confirmation` calls `guard_status` and discards it (`scripts/doe.py:1358-1371`), so the failed row still enters the mean and the `n >= 3` count. `promotion_checks` then blocks ship (`scripts/doe.py:1467-1468`). `done` stays false. The recommendation becomes `complete_promotion_contract` whenever the contaminated mean clears the bar.

Reproduction: confirmation latencies `90, 90, 90` with `guard_ok: true` miss `target: 85`. Adding `{latency: 40, guard_ok: false}` makes the mean 77.5. Screening at the chosen cell should sit near that mean so the interval also passes. `numerical_confirmed` flips true only because of the failed row. `tests/test_doe_contract.py:131-134` asserts `not done` and does not assert `numerical_confirmed` is false.

**Omitted paired guards are treated as passed.** `scripts/paired_analysis.py:30` uses `r.get('guard_ok', True)`. A missing field enters inference. Explicit `null` or `false` raises `Failed or unknown guards cannot enter paired inference`. `promotion_ready` stays false, but `supports_practical_improvement` can be true. `tests/test_paired_analysis.py:10-15` builds rows with no `guard_ok` and expects that true result. Five units, baseline 10 and candidate 13, no guard field: status `supports_practical_improvement`.

**Confirm ships one side of a near-tie and drops the tie.** `select_best` records every feasible run within 0.02 and warns (`scripts/objectives.py:616-626`). `cmd_confirm` copies those warnings only when there is no winner (`scripts/doe.py:1541-1555`). A winner returns `recommendation: "ship"` with no `contenders` field (`scripts/doe.py:1593-1627`). Identical guarded runs, contract bound to the first argmax: both scores match, and the command still ships that index alone.

## Low

**Checked-in metadata omits the new commands.** `pyproject.toml:21-30` registers analyst, paired, ledger, report, and assess. `scripts/agent_doe_engine.egg-info/entry_points.txt:1-5` and `top_level.txt` still list only doe, loop, metric_runner, objectives, suggest_factors, validate_factors, and worktree. A build that regenerates from `pyproject.toml` is unaffected. An install that reuses this egg-info drops the new CLIs.

## Checked, not reported

Report script breakout was re-read at `scripts/report.py:240` and `261-291`. Export JSON replaces `<`, `>`, and `&`; user text goes through `html.escape`; the inline script does not interpolate ledger strings. CSV prefixes `=`, `+`, `-`, and `@`. No remote script or stylesheet URL is in `scripts/report_theme.css`.

Jev stays off unless `provider` is `typesafe` and `allow_cloud` is true; the key is not read before that; results omit the key, state, and rubric prose; redirects are not followed; confidence is labeled as a model distribution. The adapter does not emit a pass/fail.

Ledger appends under an exclusive lock, rejects a broken hash, a changed `plan_hash`, a reused `attempt_id`, and a settings change for an existing batch/cell, and does not execute measurement commands. In-place value edits fail `load_ledger`.

Promotion identity checks do reject a mismatched `candidate_id`, `config_hash`, fixture, scorer, split, or an explicit failed guard on the selected run and on confirmation rows. Missing `guard_ok` blocks `promotion_ready`. Three-level factors are rejected in `validate_levels` and `analyst.check_plan`.

## Limitations

- No pytest or Node check. The shell never started.
- `recommendation: "ship"` is reachable from a self-asserted review. `promotion_checks.provenance` is `unverified_attestations`. A semantic stub `{"required": true, "status": "ok", "measurement_available": true}` is enough when the contract requires Jev; `tests/test_doe_contract.py:256-264` expects that.
- `item_ids` are optional, so omitting them skips the only mechanical split-overlap check.
- The ledger hash does not stop a full-file rewrite or tail deletion. That limit is stated in `scripts/experiment_ledger.py:6-8`. `semantic_assessor.py --append-attempt` will append an unlocked, unvalidated line to whatever path it is given, including a campaign ledger.
- Browser rendering, live TypeSafe, and an installed-wheel theme lookup were not exercised.
