import importlib.util
from pathlib import Path
import pytest

spec=importlib.util.spec_from_file_location('goal_dogfood',Path(__file__).resolve().parents[1]/'experiments/goal-alignment/run.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

@pytest.fixture(autouse=True)
def fixed_cli_version(monkeypatch):
    monkeypatch.setattr(m,'cli_version',lambda:'offline-test-cli-version')

@pytest.fixture
def case():
    return {'id':'x','system_goal':'useful answers','context':'timing','options':{'A':'a','B':'b','C':'c'},'next_tests':{'T1':'a','T2':'b','T3':'c'},'metric_choices':{'M1':'a','M2':'b','M3':'c'},'expected_choice':['B'],'expected_next_test':['T3'],'required_protected_metric':'M1','expected_interpretation':'inconclusive','unsafe_choices':['A']}

def test_gold_not_in_public_prompt(case):
    public=m.public_case(case)
    assert not (set(case)-set(m.PUBLIC)) & set(public)
    assert public['system_goal']==case['system_goal']

def test_scores_and_harm_are_separate(case):
    answer={'choice':'B','next_test':'T3','protected_metric':'M1','interpretation':'inconclusive','reason':'test'}
    assert m.score(case,answer)['decision_score']==1
    answer['choice']='C'
    assert m.score(case,answer)['safe_choice']==1
    assert m.score(case,answer)['decision_score']==.75
    answer['choice']='A'
    assert m.score(case,answer)['safe_choice']==0

def test_malformed_is_not_a_pass(case):
    with pytest.raises(ValueError):m.score(case,{'choice':'B'})
    with pytest.raises(ValueError):m.score(case,{'choice':'Z','next_test':'T3','protected_metric':'M1','interpretation':'inconclusive','reason':'test'})


@pytest.mark.parametrize('provider_output', ['[]', 'null', '{"subtype":"success","modelUsage":null}', '{"subtype":"success","modelUsage":{}}'])
def test_malformed_provider_receipt_returns_explicit_failure(monkeypatch, provider_output):
    from types import SimpleNamespace
    monkeypatch.setattr(m.subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout=provider_output, returncode=0))
    raw, answer, elapsed, error = m.invoke('public input', 'system instructions')
    assert isinstance(raw, dict) and answer is None and error
    assert elapsed >= 0


def test_provider_launch_error_returns_explicit_failure(monkeypatch):
    def fail(*args, **kwargs):
        raise OSError('missing executable')
    monkeypatch.setattr(m.subprocess, 'run', fail)
    raw, answer, _, error = m.invoke('public input', 'system instructions')
    assert raw['status'] == 'launch_error' and answer is None and error


@pytest.mark.parametrize('filename', ['design.json', 'objectives.json'])
def test_prepared_design_and_objectives_cannot_change_before_calls(tmp_path, monkeypatch, filename):
    import json
    out = tmp_path / 'campaign'
    m.main(['prepare', '--output', str(out)])
    path = out / filename
    record = json.loads(path.read_text())
    if filename == 'design.json':
        record['runs'][0]['_factors']['goal_reasoning'] = 1
    else:
        record['objectives'][0]['direction'] = 'lower'
    path.write_text(json.dumps(record))
    def never_call(*args, **kwargs):
        pytest.fail('Measured provider must not run after frozen input changes')
    monkeypatch.setattr(m, 'invoke', never_call)
    with pytest.raises(ValueError, match='Frozen input changed'):
        m.main(['screen', '--output', str(out)])


def test_failed_invocation_keeps_real_run_id_and_durable_failure(tmp_path, monkeypatch, case):
    import json
    out = tmp_path / 'campaign'
    m.main(['prepare', '--output', str(out)])
    design = json.loads((out / 'design.json').read_text())
    def fail(*args, **kwargs):
        return {'status':'launch_error'}, None, .01, 'Provider command could not start; no retry'
    monkeypatch.setattr(m, 'invoke', fail)
    response = m.execute_case(case, design['runs'][2], 'screen', out, 'role', {})
    assert response['run_id'] == 2 and response['guard_ok'] is False
    assert all(response['values'][k] is None for k in ('decision_score','all_correct','safe_choice'))
    assert response['values']['valid_response']==0
    ledger = m.load_ledger(out / 'campaign.jsonl')
    record = ledger[-1]
    assert record['cell_id'] == '2' and record['error']
    assert record['attempt_id'] == response['attempt_id']
    assert record['guard_ok'] is False
    assert record['measurements']['valid_response']['unit']=='proportion'
    assert 'structured output' in record['measurements']['valid_response']['method']
    receipt = json.loads((out / record['evidence']).read_text())
    assert receipt['error'] and receipt['provider']['status'] == 'launch_error'
    assert receipt['frozen_claude_cli_version']=='offline-test-cli-version'
    assert receipt['frozen_sha256']==m.digest(out/'frozen.json')
    assert json.loads((out / 'screen-results.jsonl').read_text()) == response


def test_actual_provider_prompt_excludes_gold_fields(tmp_path, monkeypatch, case):
    import json
    out = tmp_path / 'campaign'
    m.main(['prepare', '--output', str(out)])
    design = json.loads((out / 'design.json').read_text())
    seen = []
    def inspect_prompt(prompt, system):
        seen.append(json.loads(prompt))
        return {'status':'fixture_failure'}, None, 0, 'Offline injection probe'
    monkeypatch.setattr(m, 'invoke', inspect_prompt)
    m.execute_case(case, design['runs'][0], 'screen', out, 'role', {})
    assert len(seen) == 1 and seen[0]['scenario'] == m.public_case(case)
    for field in ('expected_choice','expected_next_test','required_protected_metric','expected_interpretation','unsafe_choices'):
        assert field not in seen[0]['scenario']


def test_model_drift_is_an_explicit_failure(monkeypatch):
    import json
    from types import SimpleNamespace
    raw = {'subtype':'success','modelUsage':{'different-model':{}},'result':'{}'}
    monkeypatch.setattr(m.subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout=json.dumps(raw), returncode=0))
    _, answer, _, error = m.invoke('public', 'system')
    assert answer is None and 'Model drift' in error



@pytest.fixture
def offline_screen(tmp_path, monkeypatch):
    import json
    cases = json.loads((Path(m.__file__).parent / 'fixtures.json').read_text())['scenarios']
    by_id = {case['id']:case for case in cases}
    seen = []
    def fixture_response(prompt, system):
        # This offline test double exercises transport/recording only. It is not
        # a measured model response and is never used by the experiment runner.
        payload = json.loads(prompt)
        public = payload['scenario']
        seen.append(public)
        case = by_id[public['id']]
        first = lambda value: value[0] if isinstance(value, list) else value
        answer = {'choice':first(case['expected_choice']),
                  'next_test':first(case['expected_next_test']),
                  'protected_metric':first(case['required_protected_metric']),
                  'interpretation':first(case['expected_interpretation']),
                  'reason':'Offline test response; no provider request.'}
        return {'total_cost_usd':0}, answer, .01, None
    monkeypatch.setattr(m, 'invoke', fixture_response)
    out = tmp_path / 'offline-campaign'
    m.main(['prepare', '--output', str(out)])
    m.main(['screen', '--output', str(out)])
    return out, seen


def test_screen_records_matrix_ids_and_freezes_selection(offline_screen):
    import json
    out, seen = offline_screen
    rows = [json.loads(line) for line in (out/'screen-results.jsonl').read_text().splitlines()]
    assert len(rows) == 24 and len(seen) == 24
    assert {r['run_id'] for r in rows} == {0,1,2,3}
    assert all(sum(r['run_id']==rid for r in rows)==6 for rid in range(4))
    selection=json.loads((out/'selection.json').read_text())
    assert selection['confirmation_challenger'] == 3  # frozen exploratory tie arm
    effects = json.loads((out/'effects.json').read_text(), parse_constant=lambda value: pytest.fail('Nonfinite strict JSON output'))
    assert 'descriptive only' in effects['serialization_note']
    assert (out/'effects-engine.txt').exists()
    decision=next(r for r in m.load_ledger(out/'campaign.jsonl') if r.get('decision_id')=='screen-to-confirm')
    assert decision['selection_sha256']==m.digest(out/'selection.json')
    assert decision['screening_sha256']==m.digest(out/'screen-results.jsonl')
    assert all('expected_choice' not in r and 'unsafe_choices' not in r for r in seen)


def test_selection_drift_blocks_confirmation_calls(offline_screen, monkeypatch):
    import json
    out,_=offline_screen
    path=out/'selection.json'
    selection=json.loads(path.read_text());selection['confirmation_challenger']=1
    path.write_text(json.dumps(selection))
    def never_call(*args,**kwargs):pytest.fail('Provider called after selection drift')
    monkeypatch.setattr(m,'invoke',never_call)
    with pytest.raises(ValueError,match='Screening or selection changed'):
        m.main(['confirm','--output',str(out)])


def test_partial_paired_confirmation_cannot_authorize_fixed_budget_inference(offline_screen):
    import json
    out,_=offline_screen
    m.main(['confirm','--output',str(out)])
    path=out/'confirm-results.jsonl'
    rows=[json.loads(line) for line in path.read_text().splitlines()]
    # Both arms still match, but only five of six declared cases remain.
    removed=rows[0]['unit_id']
    rows=[r for r in rows if r['unit_id']!=removed]
    path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
    with pytest.raises(ValueError,match='Result count differs from canonical ledger'):
        m.main(['summarize','--output',str(out)])
    assert not (out/'paired-confirmation.json').exists()


def test_frozen_plan_drift_cannot_override_ledger_plan(tmp_path,monkeypatch):
    import json
    out=tmp_path/'campaign'
    m.main(['prepare','--output',str(out)])
    path=out/'frozen.json';record=json.loads(path.read_text());record['min_effect']=0
    path.write_text(json.dumps(record))
    def never_call(*args,**kwargs):pytest.fail('Provider called after frozen plan drift')
    monkeypatch.setattr(m,'invoke',never_call)
    with pytest.raises(ValueError,match='Frozen inputs disagree'):
        m.main(['screen','--output',str(out)])


def test_same_attempt_identity_cannot_hide_confirmation_value_changes(offline_screen):
    import json
    out, _ = offline_screen
    m.main(['confirm', '--output', str(out)])
    path = out / 'confirm-results.jsonl'
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    original_ids = [row['attempt_id'] for row in rows]
    rows[0]['values']['all_correct'] = 0
    assert [row['attempt_id'] for row in rows] == original_ids
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    with pytest.raises(ValueError, match='Result content differs from canonical ledger'):
        m.main(['summarize', '--output', str(out)])
    assert not (out / 'paired-confirmation.json').exists()


def test_typed_output_uses_provider_object_and_same_schema(monkeypatch):
    import json
    from types import SimpleNamespace
    answer={'choice':'A','next_test':'T1','protected_metric':'M1','interpretation':'promising','reason':'Schema probe'}
    seen=[]
    def run(cmd,**kwargs):
        seen.append(cmd)
        return SimpleNamespace(stdout=json.dumps({'subtype':'success','modelUsage':{m.MODEL:{}},'structured_output':answer,'result':'not JSON'}),returncode=0)
    monkeypatch.setattr(m.subprocess,'run',run)
    for system in ('control',m.GOAL,m.NEXT):
        assert m.invoke('unrelated smoke',system)[1]==answer
    assert all(json.loads(cmd[cmd.index('--json-schema')+1])==m.OUTPUT_SCHEMA for cmd in seen)


def test_untyped_success_is_failure(monkeypatch):
    import json
    from types import SimpleNamespace
    monkeypatch.setattr(m.subprocess,'run',lambda *a,**kw:SimpleNamespace(stdout=json.dumps({'subtype':'success','modelUsage':{m.MODEL:{}},'result':'{}'}),returncode=0))
    assert 'structured_output' in m.invoke('public','system')[3]


def fake_rows(scores, safe=1):
    return [{'run_id':arm,'unit_id':str(i),'attempt_id':f'{arm}-{i}','guard_ok':value is not None,
        'values':{'decision_score':value,'all_correct':int(value==1) if value is not None else None,
        'safe_choice':safe if value is not None else None,'valid_response':int(value is not None),'latency_s':.1}}
        for arm,values in scores.items() for i,value in enumerate(values)]


def test_failed_baseline_cannot_create_margin_winner():
    rows=fake_rows({0:[None]*6,1:[.5]*6,2:[.5]*6,3:[1]*6})
    result=m.select_screening(rows,[{'_run_id':i} for i in range(4)],6,.05)
    assert result['means'][0] is None and result['failed_attempts']==6
    assert result['screening_winner'] is None and result['confirmation_challenger'] is None
    assert not result['clears_screening_margin'] and result['selection_status']=='baseline_incomplete'


def test_all_failed_cells_have_no_eligible_challenger():
    rows=fake_rows({i:[None]*6 for i in range(4)})
    result=m.select_screening(rows,[{'_run_id':i} for i in range(4)],6,.05)
    assert result['feasible']==[] and result['failed_attempts']==24
    assert result['confirmation_challenger'] is None


def test_primary_continuous_margin_and_secondary_binary_are_distinct():
    rows=fake_rows({0:[0]*6,3:[.5,.5,.5,.5,.75,.75]})
    result=m.confirmation_summary(rows,{},confirmation_contract())
    assert result['paired']['supports_practical_improvement']
    assert result['paired']['practical_margin']==.05
    assert result['secondary_all_correct']['p_value']==1  # neither arm fully correct
    assert result['decision']['status']=='exploratory_gain_observed'
    assert result['promotion_ready'] is False


@pytest.mark.parametrize('failure,safe,status',[(True,1,'incomplete'),(False,0,'unsafe')])
def test_confirmation_failure_or_unsafe_choice_blocks_acceptance(failure,safe,status):
    rows=fake_rows({0:[0]*6,3:[.5,.5,.5,.5,.75,None if failure else .75]},safe=safe)
    result=m.confirmation_summary(rows,{},confirmation_contract())
    assert result['decision']['status']==status
    assert not result['decision']['safe_choice_passed']
    if failure:
        assert result['paired']['status']=='invalid'
        assert result['candidate']['failed_attempts']==1 and result['candidate']['valid_attempts']==5


def test_prepare_preserves_sources_and_honest_guard_evidence(tmp_path):
    import json
    out=tmp_path/'campaign';m.main(['prepare','--output',str(out)])
    frozen=json.loads((out/'frozen.json').read_text());plan=frozen['qualified_plan']
    assert plan['system_goal']['protected_outcomes']==frozen['protected_outcomes']
    assert {o['name'] for o in plan['objectives']}=={'decision_score','safe_choice','valid_response'}
    assert frozen['confirmation']['primary_endpoint']=='decision_score'
    assert len({f['goal_contribution'] for f in plan['factors']})==2
    for path,sha in frozen['runtime']['sources'].items():assert m.digest(out/'source-snapshot'/path)==sha
    evidence=plan['objectives'][0]['validity_evidence']
    assert 'rubric-review.md sha256=' in evidence and 'test_goal_dogfood.py sha256=' in evidence
    assert 'author self-review only' in evidence and 'independently reviewed' not in evidence


@pytest.mark.parametrize('stage',['screen','confirm','summarize'])
def test_runtime_version_drift_blocks_every_stage(tmp_path,monkeypatch,stage):
    out=tmp_path/'campaign';m.main(['prepare','--output',str(out)])
    monkeypatch.setattr(m,'cli_version',lambda:'different-cli')
    with pytest.raises(ValueError,match='runtime version changed'):m.main([stage,'--output',str(out)])


def test_source_snapshot_drift_blocks_execution(tmp_path):
    out=tmp_path/'campaign';m.main(['prepare','--output',str(out)])
    (out/'source-snapshot/scripts/objectives.py').write_text('changed')
    with pytest.raises(ValueError,match='Frozen source changed'):m.main(['screen','--output',str(out)])


def test_failed_screen_preserves_all_attempts_and_counts_descriptive_scope(tmp_path,monkeypatch):
    import json
    out=tmp_path/'campaign';m.main(['prepare','--output',str(out)])
    monkeypatch.setattr(m,'invoke',lambda *a,**kw:({'status':'timeout'},None,.1,'timeout'))
    m.main(['screen','--output',str(out)])
    rows=[json.loads(line) for line in (out/'screen-results.jsonl').read_text().splitlines()]
    assert len(rows)==24 and all(r['values']['decision_score'] is None for r in rows)
    assert len([r for r in m.load_ledger(out/'campaign.jsonl') if r.get('record_type')=='attempt'])==24
    effects=json.loads((out/'effects.json').read_text())
    assert effects['status']=='not_estimable'
    assert effects['analysis_scope']['excluded_invalid_attempts']==24
    selection=json.loads((out/'selection.json').read_text())
    assert selection['confirmation_challenger'] is None and selection['failed_attempts']==24


def test_descriptive_filter_never_rewrites_canonical_results(tmp_path):
    import json
    out=tmp_path/'campaign';m.main(['prepare','--output',str(out)])
    rows=fake_rows({0:[0,None],1:[.25,.5],2:[.5,.75],3:[.75,1]})
    source=out/'screen-results.jsonl';source.write_text(''.join(json.dumps(r)+'\n' for r in rows))
    before=source.read_bytes()
    m.descriptive_effects(out,rows,[{'_run_id':i} for i in range(4)])
    assert source.read_bytes()==before
    result=json.loads((out/'effects.json').read_text())
    assert result['analysis_scope']['included_valid_attempts']==7
    assert result['analysis_scope']['excluded_invalid_attempts']==1
    assert result['campaign_eligibility_evaluated'] is False
    assert 'do not establish campaign eligibility' in result['guardrail_scope']
    assert result['failure_rates_by_cell']['0']['failure_rate']==.5
    assert result['analysis_scope']['excluded_attempt_ids']==['0-1']
    assert len((out/'screen-descriptive-valid.jsonl').read_text().splitlines())==7


def test_prepare_labels_independent_review_unperformed(tmp_path):
    import json
    out=tmp_path/'campaign';m.main(['prepare','--output',str(out)])
    readiness=json.loads((out/'readiness.json').read_text())
    assert readiness['review_status']=='unreviewed_by_independent_reviewer'
    assert 'Structural plan checks only' in readiness['readiness_scope']


def test_numpy_version_drift_blocks_stage(tmp_path,monkeypatch):
    out=tmp_path/'campaign';m.main(['prepare','--output',str(out)])
    monkeypatch.setattr(m,'numpy_version',lambda:'different-numpy')
    with pytest.raises(ValueError,match='runtime version changed'):m.main(['screen','--output',str(out)])


def confirmation_contract():
    return {'primary_endpoint':'decision_score','response':'continuous','margin':.05,
        'secondary_endpoint':'all_correct','safe_choice_required':1,'safety_applies_to':'both_arms'}


def test_subprocess_valueerror_becomes_recordable_failure(monkeypatch):
    def fail(*args,**kwargs):raise ValueError('embedded null byte')
    monkeypatch.setattr(m.subprocess,'run',fail)
    raw,answer,elapsed,error=m.invoke('public','system')
    assert raw['status']=='launch_error' and raw['stdout'] is None
    assert answer is None and error and elapsed>=0


def test_exposed_fixture_never_claims_fresh_confirmation(tmp_path):
    import json
    out=tmp_path/'campaign';m.main(['prepare','--output',str(out)])
    frozen=json.loads((out/'frozen.json').read_text())
    assert frozen['evaluation_scope']['fresh_holdout'] is False
    assert m.EXPOSED_FIXTURE_SHA256 in frozen['evaluation_scope']['known_exposed_fixture_sha256']
    assert frozen['qualified_plan']['split']['independent'] is False
    readiness=json.loads((out/'readiness.json').read_text())
    assert readiness['ready_for_execution'] is False and readiness['exploratory_execution_allowed'] is True
    for objectives in (frozen['qualified_plan']['objectives'],json.loads((out/'objectives.json').read_text())['objectives']):
        for obj in objectives:
            if obj['role'] in ('primary','guardrail'):
                assert obj['validity_independent'] is False and 'authored fixture only' in obj['validity_scope']


def test_new_script_file_is_detected(tmp_path,monkeypatch):
    import json,shutil
    out=tmp_path/'campaign';m.main(['prepare','--output',str(out)])
    frozen=json.loads((out/'frozen.json').read_text())
    fake=tmp_path/'fake-root';shutil.copytree(out/'source-snapshot',fake)
    (fake/'scripts/statistics.py').write_text('shadow = True')
    monkeypatch.setattr(m,'ROOT',fake)
    with pytest.raises(ValueError,match='scripts file set changed'):m.verify_sources(out,frozen)


def test_live_source_content_drift_is_detected(tmp_path,monkeypatch):
    import json,shutil
    out=tmp_path/'campaign';m.main(['prepare','--output',str(out)])
    frozen=json.loads((out/'frozen.json').read_text())
    fake=tmp_path/'fake-root';shutil.copytree(out/'source-snapshot',fake)
    (fake/'scripts/objectives.py').write_text('changed = True')
    monkeypatch.setattr(m,'ROOT',fake)
    with pytest.raises(ValueError,match='Frozen source changed'):m.verify_sources(out,frozen)


def test_baseline_safety_breach_names_baseline():
    rows=fake_rows({0:[0]*6,3:[.5,.5,.5,.5,.75,.75]})
    rows[0]['values']['safe_choice']=0
    result=m.confirmation_summary(rows,{},confirmation_contract())
    assert result['decision']['status']=='unsafe'
    assert result['decision']['unsafe_arms']==['baseline']
    assert 'baseline' in result['decision']['reason'] and result['decision']['safety_applies_to']=='both_arms'


def test_actual_partial_batch_writes_incomplete_report_without_inference(offline_screen,monkeypatch):
    import json
    out,_=offline_screen
    data=json.loads((Path(m.__file__).parent/'fixtures.json').read_text())
    case=next(c for c in data['scenarios'] if c['split']=='confirmation')
    run=json.loads((out/'design.json').read_text())['runs'][0]
    m.execute_case(case,run,'confirm',out,'role',data['task_contract'])
    def forbidden(*a,**kw):pytest.fail('Partial batch must not enter inference')
    monkeypatch.setattr(m,'analyze_pairs',forbidden)
    m.main(['summarize','--output',str(out)])
    summary=json.loads((out/'summary.json').read_text())
    assert summary['status']=='incomplete_batch' and summary['recorded_attempts']==1
    assert summary['expected_attempts']==12 and len(summary['missing_attempts'])==11
    assert not summary['inference_performed'] and not (out/'paired-confirmation.json').exists()
    assert (out/'report/report.html').exists()
    assert any(r.get('decision_id')=='confirmation-result' for r in m.load_ledger(out/'campaign.jsonl'))


def test_confirmation_contract_mismatch_is_rejected(tmp_path):
    import json
    out=tmp_path/'campaign';m.main(['prepare','--output',str(out)])
    frozen=json.loads((out/'frozen.json').read_text());objectives=json.loads((out/'objectives.json').read_text())
    frozen['confirmation']['margin']=.1
    with pytest.raises(ValueError,match='disagrees'):m.assert_confirmation_contract(frozen,objectives)


def test_failed_challenger_is_infeasible_but_margin_winner_is_selected():
    rows=fake_rows({0:[.25]*6,1:[.75]*6,2:[.5]*6,3:[1,1,1,1,1,None]})
    result=m.select_screening(rows,[{'_run_id':i} for i in range(4)],6,.05)
    assert 3 not in result['feasible']
    assert result['screening_winner']==1 and result['confirmation_challenger']==1
    assert result['clears_screening_margin']


def test_summarize_before_confirmation_does_not_finalize(offline_screen):
    out,_=offline_screen
    with pytest.raises(ValueError,match='has not started'):
        m.main(['summarize','--output',str(out)])
    assert not any(r.get('decision_id')=='confirmation-result' for r in m.load_ledger(out/'campaign.jsonl'))
    m.main(['confirm','--output',str(out)])
    m.main(['summarize','--output',str(out)])
    for stage in ('confirm','summarize'):
        with pytest.raises(ValueError,match='already finalized'):
            m.main([stage,'--output',str(out)])
    assert sum(r.get('decision_id')=='confirmation-result' for r in m.load_ledger(out/'campaign.jsonl'))==1


def test_added_import_package_is_detected(tmp_path,monkeypatch):
    import json,shutil
    out=tmp_path/'campaign';m.main(['prepare','--output',str(out)])
    frozen=json.loads((out/'frozen.json').read_text())
    fake=tmp_path/'fake-root';shutil.copytree(out/'source-snapshot',fake)
    shadow=fake/'scripts/objectives';shadow.mkdir();(shadow/'__init__.py').write_text('shadow=True')
    monkeypatch.setattr(m,'ROOT',fake)
    with pytest.raises(ValueError,match='scripts file set changed'):m.verify_sources(out,frozen)


def test_symlinked_import_package_is_rejected(tmp_path,monkeypatch):
    root=tmp_path/'root';(root/'scripts').mkdir(parents=True)
    external=tmp_path/'external';external.mkdir();(external/'__init__.py').write_text('shadow=True')
    (root/'scripts/objectives').symlink_to(external,target_is_directory=True)
    monkeypatch.setattr(m,'ROOT',root)
    with pytest.raises(ValueError,match='Symlinked scripts'):m.script_files()


def test_empty_confirmation_file_cannot_finalize(offline_screen):
    out,_=offline_screen
    (out/'confirm-results.jsonl').write_text('')
    with pytest.raises(ValueError,match='has not started'):
        m.main(['summarize','--output',str(out)])
    assert not (out/'summary.json').exists()
    assert not any(r.get('decision_id')=='confirmation-result' for r in m.load_ledger(out/'campaign.jsonl'))


@pytest.mark.parametrize('phase', ['screen','confirm'])
def test_lost_result_file_cannot_retry_canonical_attempt(offline_screen,monkeypatch,phase):
    out,_=offline_screen
    if phase=='confirm':m.main(['confirm','--output',str(out)])
    before={p.name:p.read_bytes() for p in (out/'receipts').iterdir()}
    (out/f'{phase}-results.jsonl').unlink()
    monkeypatch.setattr(m,'invoke',lambda *args:pytest.fail('Must block before a provider call'))
    with pytest.raises(ValueError,match='Batch already started'):m.main([phase,'--output',str(out)])
    assert before=={p.name:p.read_bytes() for p in (out/'receipts').iterdir()}


def test_existing_receipt_blocks_direct_retry(offline_screen,monkeypatch):
    import json
    out,_=offline_screen
    data=json.loads((Path(m.__file__).parent/'fixtures.json').read_text())
    case=next(c for c in data['scenarios'] if c['split']=='screening')
    run=json.loads((out/'design.json').read_text())['runs'][0]
    path=out/'receipts'/f'screen-{case["id"]}-0.json';before=path.read_bytes()
    monkeypatch.setattr(m,'invoke',lambda *args:pytest.fail('Must preserve receipt before a provider call'))
    with pytest.raises(ValueError,match='receipt already exists'):
        m.execute_case(case,run,'screen',out,'role',data['task_contract'])
    assert path.read_bytes()==before
