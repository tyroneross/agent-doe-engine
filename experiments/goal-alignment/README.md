# Test analyst decisions against the host system goal

This reproducible benchmark tests variable selection, next-test choice, protected-outcome selection and interpretation. It uses the DOE engine to compare a frozen control role with two independent prompt additions: explicit goal reasoning and an evidence/next-test protocol.

The twelve scenarios and numerical evidence are authored fixtures. Model answers are measured live through the local Claude CLI. A correct discrete answer is evidence of following the supplied scenario, not production benefit, sound free-form reasoning, or a universally better analyst.

## Run

Requires local Claude authentication for the pinned model in `run.py`; it makes up to 36 paid/model-usage calls, each capped at a $0.25 list-price estimate and 120 seconds. There is no provider fallback or automatic retry. Tools, MCP servers, customizations and session persistence are disabled.

```sh
python3 experiments/goal-alignment/run.py prepare --output .agent-doe-engine/goal-study
python3 experiments/goal-alignment/run.py screen --output .agent-doe-engine/goal-study
python3 experiments/goal-alignment/run.py confirm --output .agent-doe-engine/goal-study
python3 experiments/goal-alignment/run.py summarize --output .agent-doe-engine/goal-study
```

Review `selection.json` before confirmation. If no eligible challenger is selected, skip `confirm` and run `summarize` to record that outcome. Use a new output directory for another campaign. An interrupted batch stays incomplete; the runner refuses hidden retries or partial-batch inference. Summarizing partial confirmation saves an incomplete decision and report. An interrupted screening batch retains attempts and requires a separate report of its ledger; it cannot select a candidate.

## Evidence and controls

- `experiment-plan.json` names the purpose, harms and guardrails. Because these published cases are development data, `ready_for_execution` remains false for independent confirmation; the harness explicitly permits only exploratory execution. Other plan errors still block it.
- `frozen.json` binds fixture, role, runner, repository calculation/report scripts, fixture oracle notes, scorer tests, plan and design hashes to the campaign. Copies remain in `source-snapshot/`; every stage checks source and runtime versions. The candidate is frozen before confirmation. Hashes expose drift; they do not authenticate the author or make the runtime immutable.
- `campaign.jsonl` retains recorded responses, cost estimates, latency, failures and decisions. Before a provider call, the runner writes and syncs an exclusive intent marker under `receipts/`. If execution stops before a complete receipt and ledger entry, the outcome and cost remain unknown; the marker blocks automatic retry. Completed receipts are hash-checked before selection and confirmation analysis. An orphan receipt or marker blocks restarting the phase. These controls do not promise exactly-once provider execution across concurrent processes.
- `effects.json` contains descriptive factorial results. The engine pools heterogeneous scenario blocks; do not treat its p-values as a population-level prompt comparison. Undefined statistics are exported as null with the original engine text retained.
- The published cases have informed development. Their nominal confirmation partition is an exploratory comparison, not a fresh holdout. Reruns cannot establish confirmation of a newly tuned prompt. `paired-confirmation.json` evaluates the declared primary `decision_score` by scenario with a paired continuous interval and a 0.05 practical margin. `secondary-all-correct.json` contains the secondary McNemar comparison. A finalized result prevents further confirmation or duplicate summaries. Summarizing before any confirmation attempt asks you to run that stage first. Every confirmation choice must respect the authored boundary and every attempt must yield a valid response. Six authored cases cannot establish broad superiority or equivalence.
- `report/report.html`, CSV and Markdown show settings, method, outcomes and the next decision. The report uses the existing Calm Precision renderer.

The CLI uses one fixed JSON Schema for every arm and reads `structured_output`; free-form text is not repaired or rescored. Invalid responses have null decision metrics, a failed status and retained cost/latency. Per-arm summaries show valid and failed denominators. An incomplete screening baseline blocks selection.

Readiness checks only declarations and references. The fixture oracle notes are author review, and test files specify executable checks; neither is an independent review attestation or proof that the tests ran for a new campaign. Inspect the evidence before drawing conclusions.

The four exact fields receive equal weight. `safe_choice` means the selected option avoids a stipulated boundary violation in the authored oracle; it does not measure actual harm to users. Reasons remain review evidence and are not keyword-scored. Public prompt construction uses an allowlist and never sends the answer keys. Offline tests use explicit transport doubles and never count as measured model responses.

Raw campaigns are local ignored artifacts. Keep them with the study's provenance when sharing a conclusion; do not silently replace previous outputs.

## What the first dogfood run established

The original 36-call campaign produced 34 valid answers and two format failures. Both baseline and selected challenger answered all six confirmation cases correctly, so no prompt improvement was established. The original screening analysis incorrectly treated format failures as zero scores, and confirmation used the secondary binary endpoint. Those original results are retained with a separate post hoc correction; they are not evidence for a winning prompt.

Runner version 2 corrects failure accounting, freezes calculation sources and runtime versions, uses structured responses, and matches confirmation to the primary endpoint. Offline tests and an excluded transport smoke check validate these controls. They do not constitute another measured prompt comparison. Use fresh, harder, independently reviewed cases and an independently frozen protocol for a future confirmatory campaign; do not tune on the published confirmation keys.

The runner assumes trusted local source files. Its drift checks run before experiment work but after Python imports, and are not a security sandbox. Provider receipts retain whatever build metadata the provider supplies; a model name alone does not establish an immutable provider build.
