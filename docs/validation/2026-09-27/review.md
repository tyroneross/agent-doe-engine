# Independent review and dispositions

Date: 2026-09-27. Scope: this build's changed and new source, tests and contracts.

Two workers cross-reviewed modules they did not implement. The engine worker reviewed the analyst, paired statistics, optional semantic adapter, ledger and report. The reporting worker reviewed DOE selection and confirmation. The lead reproduced relevant behavior, coordinated fixes and ran package/browser checks. Cursor was also attempted; its [source review](cursor-review.md) completed, and its findings were reproduced or scoped explicitly.

| Finding | Correction | Verification |
|---|---|---|
| Required semantic failure lost required status when config/input was invalid | Preserve explicit required status before validation and across CLI input failures | Regression tests and independent recheck passed |
| Paired inference ignored required semantic measurement failure | Reuse the shared guard/semantic validator | Regression test and independent recheck passed |
| Legacy renderer discarded supplied measurement metadata | Preserve and validate per-metric metadata; only fill absent n | 60 report/ledger tests; independent recheck passed |
| Selected factors could disagree with coded matrix and declared levels | Bind displayed/promoted factors to the experiment design | Regression tests and independent recheck passed |
| Duplicate confirmation identities inflated measurement count | Unique attempt IDs required for promotion | Regression tests and independent recheck passed |
| Duplicate aggregated run IDs could bypass feasibility | Reject duplicate IDs in public selection API | Regression tests and independent recheck passed |
| Empty design or excessively large number escaped normal input errors | Normalize invalid data to input errors | Regression tests and independent recheck passed |

| Failed screening variance could widen confirmation intervals | Keep fit descriptive; any failed screening/confirmation guard blocks numerical confirmation, and every screening guard must pass for promotion | Independent exact Cursor fixture reproduced the old point criterion while all readiness flags correctly stayed false |
| Failed confirmation value could improve the reported decision | Explicit numerical guard-failure list and false numerical confirmation | Independent 90/90/90 plus failed 40 fixture blocked |
| Missing paired guards counted as passing | Require explicit passing guards | Negative regression test passed |
| Near-tie information disappeared during confirmation | Return contenders and selection warnings; a valid confirmed tied candidate remains eligible | Independent tied-candidate check passed |
| Tracked package metadata omitted new commands | Regenerated tracked egg-info; installed a fresh wheel | Six command entry points and packaged CSS passed |
| Semantic append could corrupt a campaign ledger | Lock and validate dedicated stream before writing | Independent refusal and missing-newline checks passed; original bytes retained |

Final disposition: no open findings in the reviewed source and local reproduction scope. Full post-fix suite: **431 passed, 1 skipped, 48 subtests passed**. The skipped test needs optional pyDOE3. Reviewers did not author the modules they independently reviewed. The lead performed actual browser and installed-wheel verification.

## Evidence limits

Cross-review establishes only the inspected source behavior and reproduced local cases. Neither reviewer ran a paid provider. Browser checks used the generated synthetic report; no claim of real campaign benefit follows. Review evidence is retained with tests so failures remain reproducible.


## Claude follow-up review

[Claude Opus 5.5 source review](claude-review.md) independently inspected the current committed code. It reported conditional approval with no contract-free promotion, export injection or unsafe cloud call. It could not execute tests or inspect the baseline diff.

- Zero minimum improvement is an explicit acceptance criterion. It permits equality; the engine now warns that no positive improvement was required. We retained the declared criterion rather than changing its meaning to strict superiority.
- Numeric guardrails apply to means of replicated responses. Requiring every noisy replicate to clear a mean threshold would change the estimand. The engine now states the threshold scope, reports individual breaches, and warns. Hard per-execution constraints belong in `guard_ok`.
- Concrete hardening: reject overlapping supplied screening/confirmation attempt IDs; handle invalid contract JSON/path and non-object confirmation values cleanly; reject malformed semantic receipts instead of showing complete; identify truncated ledger lines before parsing; return structured paired-analysis errors on overflow.

Follow-up validation: **465 passed, 1 skipped, 48 subtests passed**. Independent exact-case checks of overlapping attempts, malformed CLI inputs, mean-threshold warnings, malformed semantic receipts, truncated records and paired overflow all passed. The corrected wheel passed installed entry-point and CSS smoke checks.

Claude independently [approved the corrected source](claude-followup.md) and agreed with both explicit mean-threshold dispositions. Its optional notes are retained with their stated limits. No mandatory findings remain.
