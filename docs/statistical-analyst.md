# Plan, inspect and revise experiments

Use the host statistical analyst to define the question and propose the next batch. Use Python to validate declarations, record evidence and calculate results. The workflow runs locally with Python and numpy. TypeSafe/Jev is optional.

## 1. Qualify the plan

Start with `examples/analyst-plan.json`. Replace the synthetic scope and measurement evidence with your actual population, independent unit, factors, outcome, practical margin, guards, randomization, blocks and budget.

```sh
python3 scripts/analyst.py check --plan experiment-plan.json
```

Qualified plans require:

- `system_goal`: `purpose`, `user`, `success_criteria` and `protected_outcomes`. Each criterion/outcome has a stable `id` and `description`.
- Every primary objective links to success criterion IDs through `goal_links`.
- Every guardrail names protected outcome IDs through `protects`; every protected outcome must have guardrail coverage.
- Every factor explains `goal_contribution` and `potential_harm`, and names guardrail objective names through `guardrails`.

For example, search latency contributes to finding useful answers sooner. Retrieval relevance and accessibility protect the usefulness of that speed. An experiment that removes either must fail the corresponding guardrail. Existing plans missing this context now return readiness issues; use the updated example to migrate them. These checks validate declarations and references. The analyst still needs evidence that the objective and guardrail measure the user's actual goal.

Exit 0 means required declarations passed; exit 1 means the plan needs work; exit 2 means invalid input. Passing is not proof of scorer validity, power, human review or task independence. The analyst must inspect the named evidence. The host role is defined in `agents/statistical-analyst.md`; it does not run experiment mutations or authorize promotion.

Choose two distinct concrete levels per factor. Three-level, mixed-level, split-plot and response-surface designs need a compatible method and are not silently converted. Justify interactions before screening. Budget independently for confirmation.

## 2. Record every attempt

Initialize one append-only ledger per frozen campaign plan:

```sh
python3 scripts/experiment_ledger.py init --ledger campaign.jsonl --record campaign.json
```

`campaign.json`:

```json
{"campaign_id":"latency-001","title":"Task latency experiment","plan":{"batch_id":"screen-1","question":"Does concurrency help?"}}
```

The ledger records supplied evidence; use the separate analyst plan check to qualify the plan. Initialize with the complete validated plan in real campaigns.

`attempt.json`:

```json
{
  "attempt_id":"a-001", "batch_id":"screen-1", "cell_id":"0", "phase":"screen",
  "settings":{"workers":1,"batch":8}, "change":"Baseline settings",
  "measurements":{"latency_ms":{"method":"Monotonic timer after warmup","command":"python bench.py","unit":"ms","n":8}},
  "metrics":{"latency_ms":100.4}, "guard_ok":true,
  "executed_at":"2026-09-27T19:00:00Z"
}
```

This is an illustrative record, not a measured result. Use actual execution times, source locations and measurement counts. Missing metrics are null. A failed attempt remains in the ledger; a retry needs a new attempt ID. Settings cannot change for an existing batch/cell. Optional evidence includes raw-result URI/hash, configuration/fixture/scorer identity, provider version, cost and semantic-assessment receipt.

```sh
python3 scripts/experiment_ledger.py append-attempt --ledger campaign.jsonl --record attempt.json
```

The ledger uses a chained content hash and an exclusive append lock on Unix. Hashes detect changed records; an adversary could rewrite the whole chain. Retain an external final hash when independent audit integrity matters. Commands record supplied fields; they never execute the measurement command or prove it ran.

## 3. Inspect results and next changes

```sh
python3 scripts/report.py --ledger campaign.jsonl --output report
# Existing result files also work, with unknown metadata labeled:
python3 scripts/report.py --legacy results.jsonl --output historical-report
```

Open `report/report.html`; CSV and Markdown exports are beside it. The Calm Precision interface groups attempts in a table, uses text for status, and reveals detailed measurements and decisions on demand. It shows settings, measurement method/unit/n, metrics, guards/errors and what the next decision changed. No inference keys, server or external assets are needed.

The ledger's recording order is not proof of execution order. Reports label missing execution metadata explicitly. Failed provider calls do not become passing guards. Historical rows are labeled legacy and cannot supply missing provenance by inference.

Record a proposed decision as `decision.json`:

```json
{"decision_id":"d-001","attempt_ids":["a-001"],"reason":"The first measurement is too uncertain to select a candidate","next_change":"Repeat the frozen cells across independent tasks","evidence":"analysis.json; insufficient independent units"}
```

```sh
python3 scripts/experiment_ledger.py append-decision --ledger campaign.jsonl --record decision.json
```

Decisions are proposals. The runner applies only changes within the user's authorized scope. To change the plan, preserve the original and validate a new version:

```sh
python3 scripts/analyst.py amendment --previous experiment-plan.json \
  --proposed next-plan.json --amendment amendment.json
```

The amendment requires `previous_plan_hash`, `reason`, `evidence`, `expected_information` and `goal_impact`. Changing factors, objectives, stopping, sample budget, splits, randomization or blocking also requires `risks_and_guardrails`. Changes to `system_goal` or primary/guardrail acceptance definitions require a new campaign. Frozen acceptance includes objective membership and every primary/guardrail field except descriptions and review metadata. It also includes campaign guard logic, selection method, independent unit, target population, scorer and fixture identities. This preserves weights, thresholds and future decision fields as well as goal/protection links. An omitted role means primary. Use a new `batch_id`. Every amendment must explicitly declare the boolean `confirmation_used_for_tuning`. Moving the old confirmation split into screening requires `true` and a fresh confirmation split; previously used screening data is not fresh confirmation data. Split names remain declarations, so reviewers must also check the underlying item identities. Start a new campaign ledger for a new frozen plan and link its predecessor through the recorded decision; never overwrite a prior campaign.

## 4. Compare paired observations

Each JSONL observation has `unit_id`, `arm`, numeric `value` and an explicit `guard_ok:true`. Required semantic measurements must also be available. Missing or failed guards block inference. Both arms must have the same unit set. Continuous repeated measurements require distinct `replicate_id` values and are averaged within unit. Binary correctness allows one 0/1 observation per arm/unit.

```sh
python3 scripts/paired_analysis.py --observations pairs.jsonl \
  --baseline baseline --candidate candidate --response continuous --direction lower --margin 5
python3 scripts/paired_analysis.py --observations correctness.jsonl --response binary
```

Continuous output includes a paired Student t interval on unit means. Binary output includes exact two-sided McNemar discordance counts and p-value. Binary equality testing does not certify a nonzero practical margin. Single-unit and zero-variance samples do not receive spurious certainty. These calculations never set promotion readiness. Independence, clustering, label authority, multiple comparisons and adaptive selection remain explicit analysis responsibilities. If the independent unit is a task/repository block, aggregate or pair at that level; do not count correlated items as independent units.

## 5. Bind confirmation to the candidate

`doe.py confirm` accepts `--contract promotion-contract.json`. The contract shape is:

```json
{
  "schema_version":1,
  "candidate":{"run_id":0,"candidate_id":"candidate-0","config_hash":"actual-config-content-hash"},
  "fixture_id":"fixture-version", "scorer_id":"scorer-version",
  "objectives":[{"name":"latency_ms","role":"primary","driver":"task completion","direction":"lower","weight":1,"target":85,"validity":"validated"}],
  "selection":"scalarize",
  "split":{"screening_id":"screen-set","confirmation_id":"untouched-set","independent":true},
  "measurement":{"valid":true,"evidence":"measurement-validity-report.md"},
  "review":{"approved":true,"reviewer":"actual-independent-reviewer","evidence":"review-report.md"}
}
```

These are illustrative identities, not valid attestations for a real campaign. The objective list and selection must match the actual analysis. Add `candidate_id` and `config_hash` to the selected design run. Screening and confirmation rows record `run_id`, candidate/config identity, fixture/scorer/split identity, `guard_ok` and numeric values. Each confirmation row needs a unique nonempty `attempt_id` for promotion. Repeating a line never creates an independent attempt. The selected factor values must match the declared levels and coded matrix. If `item_ids` are present, overlapping screening/confirmation items are rejected. All required evidence must be real and reviewed; never invent an independent reviewer.

`numerical_confirmed` describes sample thresholds and prediction agreement. `promotion_ready` / `done` additionally require consistent identity, passing guards, measurement validation and independent split/review attestations. The engine labels these attestations as unauthenticated. Prediction agreement and mean thresholds do not prove statistical superiority or noninferiority. Numeric guardrails apply to the confirmation mean; individual breaches are reported separately. If every execution must satisfy a floor, make that an execution guard. A primary `min_effect:0` declares no required positive improvement; an equal-baseline candidate can meet that acceptance criterion, with a warning. A three-observation CLI minimum does not establish adequate power.

## 6. Optional Jev

`examples/semantic-assessor.json` defaults to `provider:none` and `allow_cloud:false`. Enabling requires `provider:typesafe`, `allow_cloud:true` and `TYPESAFE_API_KEY` locally. A key alone does not enable a request. Never put its value in a plan, ledger, report or command argument.

```sh
python3 scripts/semantic_assessor.py --config assessor.json \
  --state diagnostic.json --questions questions.json --append-attempt semantic-receipts.jsonl
```

State and questions use TypeSafe's typed API. For a first trial, ask one bounded question classifying acquisition, parsing, scoring, environment or unknown failure. The adapter supports validated Choice, text-level Score and Noul shapes; provider confidence is distinct from experimental uncertainty. Freeze questions/model/thresholds before comparison and calibrate against independent human labels before relying on semantic scores.

The result records status, model, question hash, latency, validated answers/usage and a sanitized error category. Status is `disabled`, `unavailable`, `error`, `unknown` or `ok`. Only validated usable answers set `measurement_available:true`; the adapter never emits a passing experiment decision. There is one bounded request with no silent retries or substitution. Raw diagnostic bodies and secrets are not written by this adapter. Its optional append file uses a Unix lock and validates the existing semantic receipt stream before writing. It refuses campaign-ledger files; attach the returned object as `semantic_assessment` in the corresponding attempt/result row.

Set `required:true` when a semantic result is necessary for measurement. Required missing/error/unknown results must block eligibility and confirmation. The contract can declare `semantic_assessor:{"required":true}` to require a receipt even if a row omitted it. No-key local DOE remains available when Jev is optional.

API contract checked September 27, 2026: [API](https://docs.typesafe.ai/api), [models](https://docs.typesafe.ai/models), [confidence](https://docs.typesafe.ai/confidence), [numerical limitations](https://docs.typesafe.ai/model-jaggedness/jev-1.13). Offline transport tests do not prove current live service behavior.

## Evaluate the analyst itself

Compare the current workflow, analyst workflow and optional analyst+Jev at equal total budgets. Use planted effects/interactions and nulls, then replay historical failures, then new tasks. Measure correctly confirmed useful changes, false promotions, effect recovery, reviewer effort, calls, cost and time. Include shuffled feedback to test whether proposals respond to evidence. Historical cases used to build the analyst cannot establish its generalization. No effectiveness improvement is claimed by shipping these tools.

## Offline walkthrough

Run `python3 examples/demo_campaign.py --output /tmp/doe-demo-UNIQUE` with a new destination. It records twelve synthetic screening attempts (including a failed cell), selects the feasible optimum, records three confirmation attempts and decisions, checks a fixture provenance contract, and renders reports. Promotion remains blocked because the failed screening cell contributes to the descriptive fitted model; investigate the failure and collect a valid new campaign before confirmation. These are labeled synthetic data and test attestations; they do not measure provider or production performance. Reusing a destination refuses to overwrite its existing ledger.
