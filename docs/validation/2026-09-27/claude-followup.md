# Claude review of corrections

Anthropic / claude-opus-5-5. Source-only review of the corrected tree and focused patch after aca356c.

**Verdict: approved. I found no remaining defects that need fixing, and I agree with both design decisions.** I confirmed from source that the patch is in the current tree (`scripts/doe.py:848-900`, `:1546-1547`, `:1563-1569`). I did not run the tests.

## The two decisions
- **Guardrail judged on the confirmation mean: agreed.** A threshold on the average is a legitimate rule as long as it's stated, and it matches how selection already works (`is_feasible` also checks per-run averages). The rule is now stated in `threshold_scope: "confirmation_mean"` on both each criterion and the top-level output. `individual_breach_count` reports runs that break the bar, and each breach adds a warning. My earlier finding 2 was about the rule being hidden, and that's resolved. The new test pins the intended behaviour: `done` stays true with one breach, which is right because the scope is declared.
- **`min_effect=0` allowing equality: agreed**, since nothing claims the candidate is better. With a zero margin the check reads as "no worse than baseline on the confirmation mean", and the warning says so.
  - One optional improvement: that fact currently only appears in the warning text. A machine-readable field on the criterion, such as `requires_positive_improvement: false`, would let automated readers of `done`/`recommendation` see it without parsing text.

## The five fixes
- **Values must be an object:** checked at `_read_confirmation`. The `except` now also covers `TypeError` and `OSError`, so list values in the design results also get a clean error.
- **Contract input:** it's now read before any other work and a bad contract returns exit code 2. `main()` at `doe.py:1793-1796` also catches remaining input errors (including `_read_design`), so the CLI doesn't show a traceback.
- **Overlapping attempt IDs:** reuse across screening and confirmation is now a blocker. Non-string IDs are rejected before being added to the set, so they can't crash the check.
- **Truncated ledger:** the incomplete-line check now runs before parsing, so both a partial JSON tail and a complete line missing its newline give the recovery message and leave the file unchanged.
- **Paired overflow:** `finite_measurement` rejects very large ints, and infinite differences or intervals are rejected. `main` now also catches `OverflowError`, and encodes the output before printing, so a failure can't leave half-written output.

## Minor notes (no action needed)
1. `analyze_pairs` called as a library function can still raise `OverflowError` from `statistics.stdev` when values are near 1e308. The CLI catches it, but library callers catching only `ValueError` won't. Fixing this is optional.
2. The ledger now rejects malformed semantic receipts, which is stricter than `objectives.guard_status`. For example, `{"required": false, "status": []}` is refused by the ledger but accepted as a result row by `doe.py`. The mismatch is harmless (it only makes the ledger stricter). But any ledger written before this patch that contains such a receipt will no longer load. The ledger seems to date from aca356c, so this probably doesn't matter unless ledgers from that commit already exist.
3. `cmd_confirm` still reads the confirmation file twice (in `_read_confirmation` and again for `promotion_checks`). If the file changes between reads, the two views could differ. That's negligible for a local CLI.

## Limits
This was a source-only review. I didn't run the tests. `fit_effects`, `predict_at`, `t_ppf` and `analyst.py` remain unreviewed, so I haven't verified that the prediction intervals are correct. Promotion still relies on the caller's word for independence and provenance, as the output says (`unverified_attestations`).

I'm Claude Opus 5.5 (`claude-opus-5-5`), made by Anthropic.
