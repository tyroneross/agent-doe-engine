import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from paired_analysis import analyze_pairs

def pairs(diffs):
    return [r for i,d in enumerate(diffs) for r in ({'unit_id':str(i),'arm':'baseline','guard_ok':True,'value':10},{'unit_id':str(i),'arm':'candidate','guard_ok':True,'value':10+d})]

def test_known_mean_interval():
    r=analyze_pairs(pairs([1,2,3,4,5]))
    assert r['improvement']==3
    assert r['interval']==pytest.approx([1.0367568,4.9632432],abs=1e-6)
    assert r['supports_practical_improvement'] is True
    assert r['promotion_ready'] is False

def test_null_and_margin():
    assert analyze_pairs(pairs([-2,-1,0,1,2]))['status']=='inconclusive'
    assert not analyze_pairs(pairs([1,2,3,4,5]),margin=2)['supports_practical_improvement']

def test_binary_historical_discordance():
    rows=[r for i in range(46) for r in ({'unit_id':str(i),'arm':'baseline','guard_ok':True,'value':int(i<42)},{'unit_id':str(i),'arm':'candidate','guard_ok':True,'value':int(i<39)})]
    r=analyze_pairs(rows,response='binary')
    assert r['p_value']==.25
    assert r['baseline_only_correct']==3
    assert not r['supports_practical_improvement']

def test_timing_repeats_do_not_inflate_n():
    rows=pairs([1,2,3])
    twice=[dict(r,replicate_id=str(j)) for r in rows for j in range(2)]
    r=analyze_pairs(twice)
    assert r['n_units']==3 and r['n_observations']==12
    assert r['interval']==analyze_pairs(rows)['interval']

@pytest.mark.parametrize('bad',[float('nan'),float('inf'),True,None])
def test_bad_values(bad):
    rows=pairs([1,2]);rows[0]['value']=bad
    with pytest.raises(ValueError):analyze_pairs(rows)

def test_missing_and_duplicate_pairs():
    with pytest.raises(ValueError):analyze_pairs(pairs([1,2])[:-1])
    with pytest.raises(ValueError):analyze_pairs(pairs([1])+pairs([1]))

def test_no_false_certainty():
    assert analyze_pairs(pairs([1]))['status']=='insufficient_independent_units'
    assert analyze_pairs(pairs([1,1,1]))['interval'] is None
    rows=pairs([1,2]);rows[0]['guard_ok']=False
    with pytest.raises(ValueError):analyze_pairs(rows)


def test_required_semantic_failure_blocks_inference():
    rows=pairs([1,2,3,4,5])
    rows[0].update(guard_ok=True,semantic_assessment={'required':True,'status':'error','measurement_available':False})
    with pytest.raises(ValueError):analyze_pairs(rows)


def test_missing_guard_is_unknown_not_passed():
    rows=pairs([1,2,3,4,5]);del rows[0]['guard_ok']
    with pytest.raises(ValueError):analyze_pairs(rows)
