#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Tyrone Ross, Jr
# SPDX-License-Identifier: Apache-2.0
"""Check experiment declarations and references; never certify semantic alignment."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import objectives
from doe import validate_levels


def plan_hash(plan):
    encoded = json.dumps(plan, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def _goal_ids(goal, field, issues):
    """Return declared IDs while reporting malformed or duplicate entries."""
    entries = goal.get(field)
    label = f'system_goal.{field}'
    if not isinstance(entries, list) or not entries:
        issues.append(f'{label}: declare at least one outcome with id and description')
        return set()
    ids = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            issues.append(f'{label}[{index}]: specify an object with id and description')
            continue
        identity = entry.get('id')
        if not _nonempty(identity):
            issues.append(f'{label}[{index}].id: specify a nonempty value')
        elif identity in ids:
            issues.append(f'{label}: duplicate id {identity!r}')
        else:
            ids.add(identity)
        if not _nonempty(entry.get('description')):
            issues.append(f'{label}[{index}].description: specify a nonempty value')
    return ids


def _references(values, allowed, label, issues):
    if not isinstance(values, list) or not values:
        issues.append(f'{label}: specify at least one reference')
        return set()
    references = set()
    for value in values:
        if not _nonempty(value):
            issues.append(f'{label}: references must be nonempty strings')
        elif value not in allowed:
            issues.append(f'{label}: unknown reference {value!r}')
        elif value in references:
            issues.append(f'{label}: duplicate reference {value!r}')
        else:
            references.add(value)
    return references


def _check_goal_links(plan, factors, objs, issues):
    goal = plan.get('system_goal')
    if not isinstance(goal, dict):
        issues.append('system_goal: declare the system purpose, user, success criteria and protected outcomes')
        goal = {}
    for field in ('purpose', 'user'):
        if not _nonempty(goal.get(field)):
            issues.append(f'system_goal.{field}: specify a nonempty value')
    successes = _goal_ids(goal, 'success_criteria', issues)
    protected = _goal_ids(goal, 'protected_outcomes', issues)
    guardrails = {o['name'] for o in objs
                  if o.get('role') == 'guardrail' and _nonempty(o.get('name'))}
    covered = set()
    for obj in objs:
        name = obj.get('name', 'objective')
        role = obj.get('role', 'primary')
        if role == 'primary':
            _references(obj.get('goal_links'), successes,
                        f'{name}.goal_links (system_goal.success_criteria)', issues)
        elif role == 'guardrail':
            covered.update(_references(obj.get('protects'), protected,
                                       f'{name}.protects (system_goal.protected_outcomes)', issues))
    for identity in sorted(protected - covered):
        issues.append(f'protected outcome {identity!r}: declare a guardrail objective that protects it')
    for index, factor in enumerate(factors):
        # validate_levels reports malformed factor objects separately.
        if not isinstance(factor, dict):
            continue
        name = factor.get('name', f'factor[{index}]')
        for field in ('goal_contribution', 'potential_harm'):
            if not _nonempty(factor.get(field)):
                issues.append(f'{name}.{field}: specify a nonempty explanation')
        _references(factor.get('guardrails'), guardrails,
                    f'{name}.guardrails (guardrail objective names)', issues)


def check_plan(plan):
    if not isinstance(plan, dict):
        raise ValueError('Plan must be an object')
    issues = []
    for field in ('question', 'target_population', 'independent_unit', 'batch_id', 'scorer_id', 'fixture_id'):
        if not _nonempty(plan.get(field)):
            issues.append(f'{field}: specify a nonempty value')
    factors = plan.get('factors', [])
    if not isinstance(factors, list) or not factors:
        issues.append('factors: specify at least one factor')
        factors = []
    try:
        validate_levels(factors)
    except ValueError as exc:
        issues.append(f'factors: {exc}')
    if plan.get('selection', 'scalarize') not in ('scalarize', 'desirability', 'pareto'):
        issues.append('selection: use scalarize, desirability or pareto')
    objs = plan.get('objectives')
    if not isinstance(objs, list) or not objs or not all(isinstance(o, dict) for o in objs):
        issues.append('objectives: specify an objective list')
        objs = []
    else:
        contract = objectives.validate_objectives(objs)
        issues.extend(f'objectives: {x}' for x in contract.get('errors', []))
        issues.extend(f'objectives: {x}' for x in contract.get('warnings', []))
    if not any(o.get('role', 'primary') == 'primary' for o in objs):
        issues.append('objectives: declare a primary outcome')
    for obj in objs:
        for field in ('name', 'unit', 'measurement_method'):
            if not _nonempty(obj.get(field)):
                issues.append(f"{obj.get('name', 'objective')}: specify {field}")
        if obj.get('role') != 'quality' and (obj.get('validity') != 'validated'
                                            or not _nonempty(obj.get('validity_evidence'))):
            issues.append(f"{obj.get('name', 'objective')}: validate the measurement with evidence")
    _check_goal_links(plan, factors, objs, issues)
    for field in ('run_budget', 'confirmation_units'):
        if type(plan.get(field)) is not int or plan[field] < 1:
            issues.append(f'{field}: specify a positive integer')
    split = plan.get('split', {})
    if not isinstance(split, dict):
        split = {}
    if (split.get('independent') is not True or not _nonempty(split.get('screening_id'))
            or not _nonempty(split.get('confirmation_id'))
            or split.get('screening_id') == split.get('confirmation_id')):
        issues.append('split: reserve independently identified confirmation data/restarts')
    for field in ('randomization', 'blocking', 'stopping_rule', 'guard'):
        if not _nonempty(plan.get(field)):
            issues.append(f'{field}: specify the procedure (including no blocking with justification)')
    return {
        'schema_version': 1, 'plan_hash': plan_hash(plan),
        'ready_for_execution': not issues, 'promotion_ready': False, 'issues': issues,
        'next_action': 'qualify_measurements_or_plan' if issues else 'freeze_plan_and_generate_design',
        'limitations': [
            'Readiness checks declarations and references; it does not prove semantic alignment with the system goal.',
            'It does not authenticate evidence or prove adequate power.',
            'The host analyst must assess goal contributions, harms, sample size, interactions and the response model.',
        ],
    }



def _acceptance_definition(plan):
    """Canonical decision criteria; auxiliary quality metrics are not acceptance."""
    objs = plan.get('objectives')
    if not isinstance(objs, list) or not all(isinstance(o, dict) for o in objs):
        return {'invalid_objectives': objs}
    metadata = {'validity_evidence', 'description',
                'validity_reviewer', 'validity_independent'}
    definitions = []
    for obj in objs:
        role = obj.get('role', 'primary')
        if role in ('primary', 'guardrail'):
            definition = {key: value for key, value in obj.items() if key not in metadata}
            definition['role'] = role
            definitions.append(definition)
    # Objective order is irrelevant. JSON keeps booleans distinct from numbers,
    # missing fields distinct from null, and duplicate declarations visible.
    return {'objectives': sorted(definitions, key=lambda value: json.dumps(value, sort_keys=True)),
            'guard': plan.get('guard'), 'selection': plan.get('selection', 'scalarize'),
            'independent_unit': plan.get('independent_unit'),
            'target_population': plan.get('target_population'),
            'scorer_id': plan.get('scorer_id'), 'fixture_id': plan.get('fixture_id')}


def check_amendment(previous, proposed, amendment):
    if not all(isinstance(x, dict) for x in (previous, proposed, amendment)):
        raise ValueError('Plans and amendment must be objects')
    issues = []
    if amendment.get('previous_plan_hash') != plan_hash(previous):
        issues.append('previous_plan_hash does not match the frozen plan')
    for field in ('reason', 'evidence', 'expected_information', 'goal_impact'):
        if not _nonempty(amendment.get(field)):
            issues.append(f'{field} is required')
    # Compare JSON representations so a boolean cannot masquerade as the number
    # 0 or 1 and bypass an amendment requirement through Python equality.
    changed = sorted(k for k in previous.keys() | proposed.keys()
                     if k not in previous or k not in proposed
                     or plan_hash(previous[k]) != plan_hash(proposed[k]))
    if not changed:
        issues.append('No plan change proposed')
    goal_changed = 'system_goal' in changed
    acceptance_changed = (plan_hash(_acceptance_definition(previous))
                          != plan_hash(_acceptance_definition(proposed)))
    if acceptance_changed:
        issues.append('Primary or guardrail acceptance definition cannot change within a campaign; start a new campaign')
    if goal_changed:
        issues.append('system_goal cannot change within a campaign; start a new campaign')
    if {'factors', 'objectives', 'stopping_rule', 'confirmation_units', 'run_budget', 'split', 'randomization', 'blocking'} & set(changed) and not _nonempty(amendment.get('risks_and_guardrails')):
        issues.append('risks_and_guardrails is required when factors, objectives, stopping, sample budget, split or allocation procedure change')
    if previous.get('batch_id') == proposed.get('batch_id'):
        issues.append('Changes require a new batch_id; never rewrite an active randomized batch')
    reused = amendment.get('confirmation_used_for_tuning')
    if type(reused) is not bool:
        issues.append('confirmation_used_for_tuning must be explicitly declared as a boolean')
    old_split = previous.get('split', {})
    new_split = proposed.get('split', {})
    old = old_split.get('confirmation_id') if isinstance(old_split, dict) else None
    old_screening = old_split.get('screening_id') if isinstance(old_split, dict) else None
    new = new_split.get('confirmation_id') if isinstance(new_split, dict) else None
    screening = new_split.get('screening_id') if isinstance(new_split, dict) else None
    inferred_reuse = _nonempty(old) and screening == old
    if inferred_reuse and reused is not True:
        issues.append('Previous confirmation data moved to screening; confirmation_used_for_tuning must be true')
    if reused is True or inferred_reuse:
        if not _nonempty(new) or new in (old, old_screening):
            issues.append('Confirmation used for tuning requires a fresh confirmation split')
    readiness = check_plan(proposed)
    issues.extend(readiness['issues'])
    return {
        'amendment_valid': not issues, 'changed_fields': changed, 'issues': issues,
        'previous_plan_hash': plan_hash(previous), 'proposed_plan_hash': plan_hash(proposed),
        'requires_new_measurement_version': bool({'objectives', 'scorer_id', 'fixture_id'} & set(changed)),
        'requires_new_campaign': goal_changed or acceptance_changed, 'promotion_ready': False,
        'limitations': readiness['limitations'] + [
            'Split IDs and reuse flags are unauthenticated declarations. This check cannot detect renamed, merged or undisclosed reused items; reviewers must verify item identities.'],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='cmd', required=True)
    check = sub.add_parser('check')
    check.add_argument('--plan', required=True)
    amend = sub.add_parser('amendment')
    amend.add_argument('--previous', required=True)
    amend.add_argument('--proposed', required=True)
    amend.add_argument('--amendment', required=True)
    args = parser.parse_args(argv)
    try:
        def read(path):
            return json.loads(Path(path).read_text())
        result = (check_plan(read(args.plan)) if args.cmd == 'check' else
                  check_amendment(read(args.previous), read(args.proposed), read(args.amendment)))
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0 if result.get('ready_for_execution', result.get('amendment_valid')) else 1
    except (ValueError, TypeError, OSError) as exc:
        print(json.dumps({'status': 'invalid', 'error': str(exc)}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
