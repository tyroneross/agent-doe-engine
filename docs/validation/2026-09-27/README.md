# Statistical analyst validation — September 27, 2026

## Implemented behavior

The host statistical analyst qualifies plans and proposes versioned amendments. The deterministic engine excludes failed guards from selection, rejects unsupported factor levels, and separates numerical confirmation from promotion readiness. Confirmation checks candidate/configuration, fixture, scorer, split, metric-validity and review attestations. The attestations remain caller supplied and unauthenticated.

An append-only ledger records attempts and decisions. The Calm Precision report displays settings, measurement method/unit/n, results, guards/errors, and the next change with its reason. Optional TypeSafe/Jev requires explicit cloud consent and a local key. No live provider inference was used during this build.

## Evidence

- [Synthetic report](synthetic-report.html): 12 screening attempts and 3 confirmation attempts from `examples/demo_campaign.py`. The failed screening cell correctly blocks numerical confirmation and promotion. The function and review oracle are explicitly synthetic; they establish workflow behavior, not production benefit.
- [Desktop render](desktop.png) and [320px render](mobile.png): source report opened in the Codex browser. Mobile document width equaled viewport width (320px); controls met 44px minimum targets. Measurement details expanded. The failed filter showed 3 attempts and its downloaded CSV contained exactly those 3 failed rows. The browser download-event listener timed out, but the actual Downloads file was read and verified.
- A fresh wheel installed in an isolated environment passed all six new/related command help checks, an analyst readiness check, DOE detect, and report generation with packaged CSS from outside the source directory.
- Full tests after review fixes: **431 passed, 1 skipped, 48 subtests passed**. The optional pyDOE3 cross-check is skipped when pyDOE3 is absent.
- Independent Cursor source review completed, followed by complementary worker cross-review and independent checks of every correction. See [review dispositions](review.md).

## Scope and limits

The calculation tests, synthetic workflow and rendered interface are separate forms of evidence. They do not prove a general campaign success rate, analyst productivity benefit, production transfer, scorer truth, or a live Jev round trip. The historical effectiveness study remains a dated research snapshot in `docs/research/2026-09-27-doe-effectiveness/` and PersonalLLMWiki.

## Learn

Preserve the declared independent unit across repeated timing measurements. Keep model confidence separate from statistical uncertainty. Label legacy provenance gaps instead of filling them by inference. Test generated JavaScript and installed CSS resources, because Python unit tests alone do not establish rendered behavior.
