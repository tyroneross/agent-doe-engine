# Intent — multi-goal plugin

## North star
A focused, standalone, host-agnostic Claude Code / Codex plugin that optimizes **multiple competing objectives** by Design of Experiments. Extracted from build-loop's single-metric `optimize`, extended with multi-objective selection.

## Update intent (this build)
v1: port build-loop's DOE + autoresearch + factor-scanner + overfitting-review, then add multi-objective optimization (scalarize / desirability / pareto) as the core differentiator. New repo at ~/dev/git-folder/multi-goal, copy-and-diverge (build-loop untouched).

## User value
- Stop single-metric tunnel vision: optimize latency AND cost AND size together, under explicit weights.
- Deterministic, reproducible experiment design; numpy-only; no vendor lock-in.
- Works the same across coding hosts (host LLM does the reasoning).

## Non-goals
- Not modifying build-loop's existing optimize code.
- Not a Bayesian/black-box optimizer (no GP/TPE); classical DOE + greedy autoresearch only in v1.
- No direct vendor API calls.

## Decisions (locked with user)
1. Destination: standalone repo + GitHub.
2. Source relation: copy & diverge.
3. Scope: full system + multi-objective.
4. Name: multi-goal.
5. Multi-objective is a v1 requirement, not roadmap.
