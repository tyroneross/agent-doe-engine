import copy
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from analyst import check_plan,check_amendment,plan_hash

def plan():
    return json.loads((Path(__file__).resolve().parents[1]/'examples/analyst-plan.json').read_text())

def test_ready_and_missing_instrument():
    p=plan();assert check_plan(p)['ready_for_execution']
    p['objectives'][0]['validity']='unvalidated'
    assert not check_plan(p)['ready_for_execution']

def test_no_holdout_and_unsupported_levels():
    p=plan();p['split']['confirmation_id']=p['split']['screening_id']
    p['factors'][0]={'name':'model','levels':['a','b','c']}
    r=check_plan(p);assert len(r['issues'])>=2

def test_amendment_is_versioned():
    p=plan();q=copy.deepcopy(p);q['run_budget']=64
    a={'previous_plan_hash':plan_hash(p),'reason':'uncertain effect','evidence':'analysis-v1','expected_information':'tighter interval'}
    assert not check_amendment(p,q,a)['amendment_valid']
    q['batch_id']='repeat-2'
    assert check_amendment(p,q,a)['amendment_valid']
    a['confirmation_used_for_tuning']=True
    assert not check_amendment(p,q,a)['amendment_valid']
    q['split']['confirmation_id']='fresh-v2'
    assert check_amendment(p,q,a)['amendment_valid']

def test_stale_hash_rejected():
    p=plan();q=copy.deepcopy(p);q['batch_id']='next'
    assert not check_amendment(p,q,{'previous_plan_hash':'wrong'})['amendment_valid']


def test_readiness_matches_engine_levels():
    p=plan();p['factors'][0]={'name':'model','levels':[{'model':'a'},{'model':'b'}]}
    assert not check_plan(p)['ready_for_execution']
    p['factors'][0]={'name':'model','levels':['a','b'],'low':0,'high':1}
    assert not check_plan(p)['ready_for_execution']


def test_malformed_amendment_is_rejected_without_crashing():
    p=plan();q=copy.deepcopy(p);q['batch_id']='next';q['split']=None
    a={'previous_plan_hash':plan_hash(p),'reason':'reason','evidence':'evidence','expected_information':'information','confirmation_used_for_tuning':True}
    assert not check_amendment(p,q,a)['amendment_valid']
    a['confirmation_used_for_tuning']='false'
    assert not check_amendment(p,q,a)['amendment_valid']
