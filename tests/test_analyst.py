# SPDX-FileCopyrightText: 2026 Tyrone Ross, Jr
# SPDX-License-Identifier: Apache-2.0
import copy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from analyst import check_plan, check_amendment, plan_hash, main


def plan():
    return json.loads((Path(__file__).resolve().parents[1] / 'examples/analyst-plan.json').read_text())


def amendment(previous):
    return {
        'previous_plan_hash': plan_hash(previous), 'reason': 'uncertain latency effect',
        'evidence': 'analysis-v1', 'expected_information': 'tighter interval',
        'goal_impact': 'A repeat batch tests whether users receive correct task results sooner.',
        'confirmation_used_for_tuning': False,
    }


def assert_issue(candidate, text):
    result = check_plan(candidate)
    assert not result['ready_for_execution']
    assert any(text in issue for issue in result['issues']), result['issues']
    return result


def test_ready_and_missing_instrument():
    candidate = plan()
    assert check_plan(candidate)['ready_for_execution']
    candidate['objectives'][0]['validity'] = 'unvalidated'
    assert_issue(candidate, 'validate the measurement')


def test_no_holdout_and_unsupported_levels():
    candidate = plan()
    candidate['split']['confirmation_id'] = candidate['split']['screening_id']
    candidate['factors'][0] = {'name': 'model', 'levels': ['a', 'b', 'c']}
    assert len(check_plan(candidate)['issues']) >= 2


def test_amendment_is_versioned():
    previous = plan()
    proposed = copy.deepcopy(previous)
    proposed['run_budget'] = 64
    record = dict(amendment(previous), risks_and_guardrails='Budget change with unchanged thresholds and independent units.')
    assert not check_amendment(previous, proposed, record)['amendment_valid']
    proposed['batch_id'] = 'repeat-2'
    result = check_amendment(previous, proposed, record)
    assert result['amendment_valid'] and not result['requires_new_campaign']
    record['confirmation_used_for_tuning'] = True
    assert not check_amendment(previous, proposed, record)['amendment_valid']
    proposed['split']['confirmation_id'] = 'fresh-v2'
    assert check_amendment(previous, proposed, record)['amendment_valid']


def test_stale_hash_rejected():
    previous = plan()
    proposed = copy.deepcopy(previous)
    proposed['batch_id'] = 'next'
    assert not check_amendment(previous, proposed, {'previous_plan_hash': 'wrong'})['amendment_valid']


def test_readiness_matches_engine_levels():
    candidate = plan()
    candidate['factors'][0] = {'name': 'model', 'levels': [{'model': 'a'}, {'model': 'b'}]}
    assert_issue(candidate, 'levels must be finite scalars')
    candidate['factors'][0] = {'name': 'model', 'levels': ['a', 'b'], 'low': 0, 'high': 1}
    assert_issue(candidate, 'not both')


def test_malformed_amendment_is_rejected_without_crashing():
    previous = plan()
    proposed = copy.deepcopy(previous)
    proposed.update(batch_id='next', split=None)
    record = dict(amendment(previous), confirmation_used_for_tuning=True, risks_and_guardrails='Fresh holdout required.')
    assert not check_amendment(previous, proposed, record)['amendment_valid']
    record['confirmation_used_for_tuning'] = 'false'
    assert not check_amendment(previous, proposed, record)['amendment_valid']


def test_legacy_plan_requires_explicit_goal_declarations():
    candidate = plan()
    del candidate['system_goal']
    for factor in candidate['factors']:
        for field in ('goal_contribution', 'potential_harm', 'guardrails'):
            del factor[field]
    candidate['objectives'][0].pop('goal_links')
    candidate['objectives'][1].pop('protects')
    result = assert_issue(candidate, 'system_goal: declare')
    for field in ('goal_links', 'protects', 'goal_contribution', 'potential_harm', 'guardrails'):
        assert any(field in issue for issue in result['issues'])


@pytest.mark.parametrize('field', ['purpose', 'user'])
@pytest.mark.parametrize('value', [None, '', '   ', [], True])
def test_goal_requires_purpose_and_user(field, value):
    candidate = plan()
    candidate['system_goal'][field] = value
    assert_issue(candidate, f'system_goal.{field}')


@pytest.mark.parametrize('field', ['success_criteria', 'protected_outcomes'])
@pytest.mark.parametrize('value', [None, [], {}, 'outcome', [None], [{'id': 'only-id'}],
                                   [{'id': [], 'description': 'Cannot reference a list'}]])
def test_goal_outcomes_require_structured_nonempty_entries(field, value):
    candidate = plan()
    candidate['system_goal'][field] = value
    assert_issue(candidate, f'system_goal.{field}')


@pytest.mark.parametrize('field', ['success_criteria', 'protected_outcomes'])
def test_duplicate_goal_identity_is_rejected(field):
    candidate = plan()
    candidate['system_goal'][field].append(copy.deepcopy(candidate['system_goal'][field][0]))
    assert_issue(candidate, 'duplicate id')


@pytest.mark.parametrize('objective_index,field', [(0, 'goal_links'), (1, 'protects')])
@pytest.mark.parametrize('value', [None, [], 'responsive-task-completion', ['unknown'], [True], [{}]])
def test_objective_links_must_reference_declared_outcomes(objective_index, field, value):
    candidate = plan()
    candidate['objectives'][objective_index][field] = value
    assert_issue(candidate, field)


def test_primary_cannot_reference_protected_outcome_as_success():
    candidate = plan()
    candidate['objectives'][0]['goal_links'] = ['correct-task-result']
    assert_issue(candidate, 'goal_links (system_goal.success_criteria): unknown reference')


def test_guardrail_cannot_reference_success_as_protection():
    candidate = plan()
    candidate['objectives'][1]['protects'] = ['responsive-task-completion']
    assert_issue(candidate, 'protects (system_goal.protected_outcomes): unknown reference')


def test_every_protected_outcome_needs_a_guardrail():
    candidate = plan()
    candidate['system_goal']['protected_outcomes'].append(
        {'id': 'private-task-data', 'description': 'Task data stays private.'})
    assert_issue(candidate, "protected outcome 'private-task-data': declare a guardrail")


def test_quality_metric_does_not_cover_a_protected_outcome():
    candidate = plan()
    candidate['objectives'][1]['role'] = 'quality'
    result = assert_issue(candidate, 'declare a guardrail objective that protects it')
    assert any('guardrails' in issue for issue in result['issues'])


@pytest.mark.parametrize('field', ['goal_contribution', 'potential_harm'])
@pytest.mark.parametrize('value', [None, '', ' ', [], True])
def test_each_factor_needs_contribution_and_harm_explanation(field, value):
    candidate = plan()
    candidate['factors'][1][field] = value
    assert_issue(candidate, f'batch.{field}')


@pytest.mark.parametrize('value', [None, [], 'correctness', ['unknown'], ['latency_ms'], [{}]])
def test_factor_guardrails_reference_guardrail_objectives(value):
    candidate = plan()
    candidate['factors'][0]['guardrails'] = value
    assert_issue(candidate, 'workers.guardrails')


def test_multiple_protected_outcomes_can_share_one_guardrail():
    candidate = plan()
    candidate['system_goal']['protected_outcomes'].append(
        {'id': 'complete-results', 'description': 'Every requested task has a result.'})
    candidate['objectives'][1]['protects'].append('complete-results')
    assert check_plan(candidate)['ready_for_execution']


def test_valid_declarations_do_not_claim_semantic_alignment():
    candidate = plan()
    candidate['factors'][0]['goal_contribution'] = 'This explanation still needs human review.'
    result = check_plan(candidate)
    assert result['ready_for_execution'] and not result['promotion_ready']
    assert any('does not prove semantic alignment' in item for item in result['limitations'])


def test_goal_change_requires_a_new_campaign_even_with_new_batch():
    previous = plan()
    proposed = copy.deepcopy(previous)
    proposed['batch_id'] = 'next'
    proposed['system_goal']['purpose'] = 'Maximize throughput regardless of task correctness.'
    record = dict(amendment(previous), risks_and_guardrails='Existing correctness guard remains.')
    result = check_amendment(previous, proposed, record)
    assert not result['amendment_valid'] and result['requires_new_campaign']
    assert 'system_goal cannot change within a campaign; start a new campaign' in result['issues']


def test_weakening_protected_outcome_is_a_goal_change():
    previous = plan()
    proposed = copy.deepcopy(previous)
    proposed['batch_id'] = 'next'
    proposed['system_goal']['protected_outcomes'][0]['description'] = 'Most task results should be correct.'
    result = check_amendment(previous, proposed, amendment(previous))
    assert not result['amendment_valid'] and result['requires_new_campaign']


@pytest.mark.parametrize('value', [None, '', ' ', {}, True])
def test_every_amendment_requires_goal_impact(value):
    previous = plan()
    proposed = copy.deepcopy(previous)
    proposed['batch_id'] = 'next'
    record = dict(amendment(previous), goal_impact=value)
    result = check_amendment(previous, proposed, record)
    assert not result['amendment_valid'] and 'goal_impact is required' in result['issues']


@pytest.mark.parametrize('field', ['factors', 'objectives'])
def test_factor_or_objective_change_requires_risk_explanation(field):
    previous = plan()
    proposed = copy.deepcopy(previous)
    proposed['batch_id'] = 'next'
    if field == 'factors':
        proposed[field][0]['high'] = 8
    else:
        proposed[field][0]['description'] = 'Clarified metric documentation'
    record = amendment(previous)
    result = check_amendment(previous, proposed, record)
    assert not result['amendment_valid']
    assert 'risks_and_guardrails is required when factors, objectives, stopping, sample budget, split or allocation procedure change' in result['issues']
    record['risks_and_guardrails'] = 'More concurrency can corrupt results; retain the exact correctness guard.'
    assert check_amendment(previous, proposed, record)['amendment_valid']


def test_amendment_cannot_drop_protected_guardrail_even_with_risk_prose():
    previous = plan()
    proposed = copy.deepcopy(previous)
    proposed['batch_id'] = 'next'
    proposed['objectives'] = proposed['objectives'][:1]
    record = dict(amendment(previous), risks_and_guardrails='Accept all risks to improve latency.')
    result = check_amendment(previous, proposed, record)
    assert not result['amendment_valid']
    assert any('declare a guardrail objective that protects it' in issue for issue in result['issues'])


def test_cli_legacy_plan_returns_informative_not_ready(tmp_path, capsys):
    candidate = plan()
    del candidate['system_goal']
    source = tmp_path / 'plan.json'
    source.write_text(json.dumps(candidate))
    assert main(['check', '--plan', str(source)]) == 1
    output = capsys.readouterr()
    assert not output.err
    assert not json.loads(output.out)['ready_for_execution']
    assert 'system_goal' in output.out



def test_boolean_factor_value_cannot_bypass_change_review():
    previous = plan()
    proposed = copy.deepcopy(previous)
    proposed['batch_id'] = 'next'
    proposed['factors'][0]['low'] = True  # True == 1 in Python, distinct JSON values.
    result = check_amendment(previous, proposed, amendment(previous))
    assert 'factors' in result['changed_fields']
    assert not result['amendment_valid']
    assert 'risks_and_guardrails is required when factors, objectives, stopping, sample budget, split or allocation procedure change' in result['issues']


def test_goal_metadata_type_change_cannot_bypass_campaign_boundary():
    previous = plan()
    previous['system_goal']['version'] = 1
    proposed = copy.deepcopy(previous)
    proposed['batch_id'] = 'next'
    proposed['system_goal']['version'] = True
    result = check_amendment(previous, proposed, amendment(previous))
    assert not result['amendment_valid'] and result['requires_new_campaign']


@pytest.mark.parametrize('index', [0, 1])
@pytest.mark.parametrize('field,value', [
    ('name', 'changed-name'), ('direction', 'higher'), ('baseline', 50),
    ('target', 50), ('min_effect', 0), ('min_acceptable', 0.5),
    ('goal_links', ['different-success']), ('protects', ['different-protection']),
])
def test_acceptance_changes_require_new_campaign_despite_risk_prose(index, field, value):
    previous = plan()
    proposed = copy.deepcopy(previous)
    proposed['batch_id'] = 'next'
    if field == 'direction':
        value = 'higher' if previous['objectives'][index]['direction'] == 'lower' else 'lower'
    proposed['objectives'][index][field] = value
    record = dict(amendment(previous), risks_and_guardrails='Accept risk and retain guardrails.')
    result = check_amendment(previous, proposed, record)
    assert not result['amendment_valid'] and result['requires_new_campaign']
    assert any('acceptance definition cannot change' in issue for issue in result['issues'])


@pytest.mark.parametrize('index', [0, 1])
@pytest.mark.parametrize('change', ['remove', 'add', 'quality', 'swap'])
def test_decision_objective_membership_is_frozen(index, change):
    previous = plan()
    proposed = copy.deepcopy(previous)
    proposed['batch_id'] = 'next'
    if change == 'remove':
        proposed['objectives'].pop(index)
    elif change == 'add':
        extra = copy.deepcopy(proposed['objectives'][index])
        extra['name'] = 'extra-decision-metric'
        proposed['objectives'].append(extra)
    elif change == 'quality':
        proposed['objectives'][index]['role'] = 'quality'
    else:
        proposed['objectives'][index]['role'] = 'guardrail' if index == 0 else 'primary'
    result = check_amendment(previous, proposed, dict(amendment(previous), risks_and_guardrails='Reviewed risks.'))
    assert not result['amendment_valid'] and result['requires_new_campaign']


@pytest.mark.parametrize('index,field', [(0, 'min_effect'), (1, 'min_acceptable'), (0, 'goal_links'), (1, 'protects')])
def test_removing_acceptance_field_requires_new_campaign(index, field):
    previous = plan()
    proposed = copy.deepcopy(previous)
    proposed['batch_id'] = 'next'
    del proposed['objectives'][index][field]
    result = check_amendment(previous, proposed, dict(amendment(previous), risks_and_guardrails='Reviewed risks.'))
    assert not result['amendment_valid'] and result['requires_new_campaign']


def test_quality_only_addition_and_objective_reordering_preserve_acceptance():
    previous = plan()
    proposed = copy.deepcopy(previous)
    proposed['batch_id'] = 'next'
    proposed['objectives'].reverse()
    proposed['objectives'].append({'name':'cost', 'role':'quality', 'direction':'lower',
                                   'unit':'USD', 'measurement_method':'Provider receipt'})
    result = check_amendment(previous, proposed, dict(amendment(previous), risks_and_guardrails='Cost is descriptive only.'))
    assert result['amendment_valid'] and not result['requires_new_campaign']


def test_omitted_role_means_primary_for_readiness_and_frozen_acceptance():
    previous = plan()
    previous['objectives'][0].pop('role')
    assert check_plan(previous)['ready_for_execution']
    proposed = copy.deepcopy(previous)
    proposed['batch_id'] = 'next'
    proposed['objectives'][0]['role'] = 'primary'
    record = dict(amendment(previous), risks_and_guardrails='Only make the default role explicit.')
    result = check_amendment(previous, proposed, record)
    assert result['amendment_valid'] and not result['requires_new_campaign']
    proposed['objectives'][0]['min_effect'] = 0
    assert check_amendment(previous, proposed, record)['requires_new_campaign']


def test_boolean_alias_cannot_mask_acceptance_change():
    previous = plan()
    proposed = copy.deepcopy(previous)
    proposed['batch_id'] = 'next'
    proposed['objectives'][1]['min_acceptable'] = True
    result = check_amendment(previous, proposed, dict(amendment(previous), risks_and_guardrails='Reviewed risks.'))
    assert result['requires_new_campaign'] and not result['amendment_valid']


@pytest.mark.parametrize('value', [None, 'false', 0, 1, [], {}])
def test_confirmation_reuse_flag_requires_explicit_boolean(value):
    previous = plan()
    proposed = copy.deepcopy(previous)
    proposed['batch_id'] = 'next'
    record = amendment(previous)
    if value is None:
        record.pop('confirmation_used_for_tuning')
    else:
        record['confirmation_used_for_tuning'] = value
    result = check_amendment(previous, proposed, record)
    assert not result['amendment_valid']
    assert any('explicitly declared as a boolean' in issue for issue in result['issues'])


@pytest.mark.parametrize('declared_reuse', [None, False, True])
@pytest.mark.parametrize('fresh', [False, True])
def test_old_holdout_moved_to_screening_requires_disclosure_and_fresh_split(declared_reuse, fresh):
    previous = plan()
    proposed = copy.deepcopy(previous)
    proposed['batch_id'] = 'next'
    proposed['split']['screening_id'] = previous['split']['confirmation_id']
    if fresh:
        proposed['split']['confirmation_id'] = 'fresh-holdout'
    record = dict(amendment(previous), risks_and_guardrails='Move development data and reserve fresh confirmation.')
    if declared_reuse is None:
        record.pop('confirmation_used_for_tuning')
    else:
        record['confirmation_used_for_tuning'] = declared_reuse
    result = check_amendment(previous, proposed, record)
    assert result['amendment_valid'] is (declared_reuse is True and fresh)
    if not fresh:
        assert 'Confirmation used for tuning requires a fresh confirmation split' in result['issues']
    if declared_reuse is not True:
        assert any('moved to screening' in issue for issue in result['issues'])


@pytest.mark.parametrize('role', ['primary', 'guardrail'])
def test_quality_metric_cannot_become_an_acceptance_metric(role):
    previous = plan()
    previous['objectives'].append({'name':'extra', 'role':'quality', 'direction':'higher',
                                   'unit':'fraction', 'measurement_method':'Exact comparison'})
    proposed = copy.deepcopy(previous)
    proposed['batch_id'] = 'next'
    proposed['objectives'][-1]['role'] = role
    result = check_amendment(previous, proposed, dict(amendment(previous), risks_and_guardrails='Reviewed risks.'))
    assert result['requires_new_campaign'] and not result['amendment_valid']


def test_swapping_screen_and_holdout_ids_does_not_create_fresh_confirmation():
    previous = plan()
    proposed = copy.deepcopy(previous)
    proposed['batch_id'] = 'next'
    proposed['split']['screening_id'] = previous['split']['confirmation_id']
    proposed['split']['confirmation_id'] = previous['split']['screening_id']
    record = dict(amendment(previous), confirmation_used_for_tuning=True, risks_and_guardrails='Fresh holdout required.')
    result = check_amendment(previous, proposed, record)
    assert not result['amendment_valid']
    assert 'Confirmation used for tuning requires a fresh confirmation split' in result['issues']


@pytest.mark.parametrize('field,value', [
    ('guard','Allow one boundary breach'), ('selection','pareto'),
    ('independent_unit','item instead of repository'),
    ('target_population','Exclude difficult tasks'),
])
def test_campaign_acceptance_fields_are_frozen(field, value):
    previous = plan()
    proposed = copy.deepcopy(previous)
    proposed.update(batch_id='next', **{field:value})
    result = check_amendment(previous, proposed, amendment(previous))
    assert not result['amendment_valid'] and result['requires_new_campaign']


@pytest.mark.parametrize('field,value', [('weight', 12), ('future_threshold', 0.1)])
@pytest.mark.parametrize('index', [0, 1])
def test_unknown_objective_decision_fields_are_frozen(field, value, index):
    previous = plan()
    proposed = copy.deepcopy(previous)
    proposed['batch_id'] = 'next'
    proposed['objectives'][index][field] = value
    result = check_amendment(previous, proposed, dict(amendment(previous), risks_and_guardrails='Reviewed.'))
    assert not result['amendment_valid'] and result['requires_new_campaign']


@pytest.mark.parametrize('field,value', [('stopping_rule','Stop after two batches'), ('confirmation_units', 12)])
def test_adaptive_sample_or_stopping_changes_require_risk_statement(field, value):
    previous = plan()
    proposed = copy.deepcopy(previous)
    proposed.update(batch_id='next', **{field:value})
    record = amendment(previous)
    assert not check_amendment(previous, proposed, record)['amendment_valid']
    record['risks_and_guardrails'] = 'Preserve decision thresholds and reserve independent confirmation.'
    assert check_amendment(previous, proposed, record)['amendment_valid']


def test_item_identity_limitation_is_machine_visible():
    previous = plan()
    proposed = copy.deepcopy(previous)
    proposed['batch_id'] = 'next'
    result = check_amendment(previous, proposed, amendment(previous))
    assert any('renamed, merged or undisclosed reused items' in text for text in result['limitations'])


@pytest.mark.parametrize('field', ['scorer_id','fixture_id'])
def test_measurement_identity_requires_new_campaign(field):
    previous=plan(); proposed=copy.deepcopy(previous)
    proposed.update(batch_id='next', **{field:'new-instrument'})
    result=check_amendment(previous,proposed,dict(amendment(previous),risks_and_guardrails='Reviewed.'))
    assert result['requires_new_campaign'] and not result['amendment_valid']


def test_measurement_method_cannot_silently_redefine_metric():
    previous=plan(); proposed=copy.deepcopy(previous);proposed['batch_id']='next'
    proposed['objectives'][0]['measurement_method']='Drop slow executions'
    result=check_amendment(previous,proposed,dict(amendment(previous),risks_and_guardrails='Reviewed.'))
    assert result['requires_new_campaign'] and not result['amendment_valid']


@pytest.mark.parametrize('field,value', [('run_budget',200),('randomization','Fixed order'),('blocking','No blocking')])
def test_budget_allocation_changes_need_risk_statement(field,value):
    previous=plan();proposed=copy.deepcopy(previous);proposed.update(batch_id='next',**{field:value})
    assert not check_amendment(previous,proposed,amendment(previous))['amendment_valid']
