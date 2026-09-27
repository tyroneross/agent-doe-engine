# Claude independent source review

Anthropic / claude-opus-5-5. Reviewed aca356c source; no shell, baseline diff or tests were available to the reviewer. Dispositions are recorded in review.md.

**Verdict: conditional approve. No finding lets a run be marked `done`/`ship` without the promotion contract.** I found no export injection or unsafe cloud call. Two issues are about what "passing" means and are worth fixing before relying on `ship`; the rest are hardening.

I reviewed the current tree only. With just Read/Glob/Grep I couldn't diff against 6baace4, and I didn't run any tests.

## Findings

**1. Medium: a primary objective with `min_effect: 0` can ship with no improvement.** (`scripts/doe.py:854-856`, `:1447-1449`, `scripts/objectives.py:359`)
- `validate_objectives` accepts `min_effect >= 0`, and `promotion_checks` accepts `baseline` plus `min_effect` as enough for a primary.
- `confirm_objective` then passes when `improvement >= min_effect`. With a zero margin, a candidate that exactly equals the baseline passes. The check uses the point estimate only, with no uncertainty.
- **Repro:** use `{"name":"latency","direction":"lower","role":"primary","baseline":100,"min_effect":0}`, confirmation values `[100, 100, 100]` whose mean falls inside the prediction interval, and a complete contract. The primary passes and the recommendation is `ship`.
- **Fix:** require `min_effect > 0` for primaries in `promotion_checks`, or use a strict `>` comparison.

**2. Medium: guardrails are checked on the confirmation mean, not on each run.** (`scripts/doe.py:868-874`)
- `passed = meets(mean, bar)`, so single confirmation runs can break `min_acceptable` as long as the average holds.
- **Repro:** a guardrail with `direction:"higher"` and `min_acceptable:0.9`, confirmation values `[0.99, 0.99, 0.80]`. The mean is 0.927, so the guardrail passes and `done` can be true even though one run broke the floor.
- The output labels this `point_estimate`, but "guardrail held" normally means no run fell below the floor.
- **Fix:** also require every confirmation value to meet the bar, or at least report per-run breaches as a blocker.

**3. Low: confirmation rows are never checked against screening rows.** (`scripts/doe.py:1495-1526`)
- `attempt_id` is only checked for uniqueness within `confirm.jsonl`.
- **Repro:** copy the best run's screening rows into the confirmation file, relabel `split_id`, add fresh `attempt_id`s and leave out `item_ids`. Every blocker passes.
- The output says it relies on caller attestations (`unverified_attestations`), but a cross-split `attempt_id` check is cheap and catches this accidental reuse.

**4. Low: malformed inputs crash with a traceback instead of a clean error or blocker.**
- `doe.py:1633`: `_read_json_arg(contract_arg)` is outside any `try`, so a bad contract JSON or path exits via traceback rather than returning a blocker.
- `doe.py:1389-1397`: if `"values"` is a list or string, `vals[n]` raises `TypeError`, which the `except (ValueError, KeyError)` at `:1583` doesn't catch.
- Neither causes a false promotion.

**5. Low: the report can call an attempt "complete" when its semantic receipt is malformed.** (`scripts/report.py:140-145`, `scripts/experiment_ledger.py:93-128`)
- The ledger never validates `semantic_assessment`. A receipt like `{"required":"true","status":"error"}` has a non-boolean `required`, so `_semantic_issue` ignores it and `status()` returns `complete`.
- `objectives.guard_status` rejects the same row outright.
- **Fix:** validate the receipt shape in the ledger, or treat a non-boolean `required` as an issue.

**6. Low: a truncated last ledger line gives the wrong error.** (`scripts/experiment_ledger.py:191-197`)
- A crash mid-write leaves a partial JSON line. `_read_lines` fails on it first, so the recovery message at `:196-197` only appears when the last line is complete JSON missing its newline.
- Appends are still refused, so nothing is lost; users just get a less helpful message.

**7. Low: very large values crash `paired_analysis`.** (`scripts/paired_analysis.py:102`)
- Differences like `1e308 - (-1e308)` overflow to `inf`. `json.dumps(..., allow_nan=False)` sits outside the `try`, so the CLI tracebacks instead of returning `status: invalid`.

## Checked and OK
- **Selection:** failed guards are removed before selection and duplicate `run_id`s are rejected. Replicate guards are combined conservatively (any failure fails the cell). Unknown guards stay eligible but can't pass the screening check in `promotion_checks`.
- **Numeric guards:** booleans, NaN/inf and overflow are rejected, as are negative weights, `min_effect` or `noise_floor`. Alpha must be finite and in (0, 1). Contract objectives and selection must match the analysis exactly. The selected run must match the design matrix, and `done` requires at least 3 confirmation runs.
- **Paired analysis:** units must match across arms and none are silently dropped. Replicate-identity rules hold, failed or unknown guards are rejected, and the interval test is conservative.
- **Semantic assessor:** it only contacts the cloud when `provider=typesafe` and `allow_cloud is True`, and reads the key only after that. The endpoint is fixed, redirects are refused, and request/response sizes are capped. Response parsing is strict (duplicate keys and NaN rejected). Only sanitized results are saved, identified by a question hash; state, credentials and error text are never persisted.
  - The HTTP timeout applies per socket operation, not to the whole call, so a slow server can take longer than `timeout_seconds` (still bounded by the size cap).
- **Report and export:** CSV cells are prefixed against formula injection, including after stripping leading whitespace, and the filtered JavaScript export does the same. HTML and Markdown are escaped, and the embedded JSON has `<`, `>` and `&` escaped.
  - `write_report` overwrites any existing `report.*` in the output folder without checking first. That's expected behaviour, but not atomic.
- **Ledger:** appends are locked, fsynced and validated before writing, and use a hash chain. The docs are clear it detects edits but can't stop someone rewriting the whole file.

## Limits
I didn't read `fit_effects`, `predict_at`, `doe_stats.t_ppf` or `scripts/analyst.py`, and only glanced at the tests. Prediction-interval correctness is therefore not verified.

I'm Claude, made by Anthropic, running as Opus 5.5 (`claude-opus-5-5`).
