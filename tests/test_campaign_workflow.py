import importlib.util
from pathlib import Path
import json
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('demo',ROOT/'examples/demo_campaign.py')
demo=importlib.util.module_from_spec(spec);spec.loader.exec_module(demo)

def test_complete_synthetic_workflow(tmp_path):
    result=demo.build(tmp_path)
    assert not result['numerical_confirmed'] and not result['promotion_ready']
    assert not result['done']
    analysis=json.loads((tmp_path/'analysis.json').read_text())
    assert analysis['best_run']==2
    assert 3 not in analysis['selection']['feasible_run_ids']
    html=(tmp_path/'report.html').read_text()
    assert 'Synthetic DOE example' in html
    assert 'fixture-complete' in html
    assert 'synthetic ms' in html
