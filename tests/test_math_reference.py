"""Independent published examples and an optional live base-R calculation check."""
import importlib.util
from pathlib import Path
import shutil
import pytest

spec = importlib.util.spec_from_file_location('math_reference', Path(__file__).resolve().parents[1] / 'experiments/math-reference/check.py')
reference = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reference)


def test_nist_published_yates_coefficients():
    # NIST eda35i: these are coded regression coefficients, half the
    # low-to-high factorial effects. The fitted model omits the 3-way term.
    values, fit = reference.calculate()
    expected = {'(Intercept)':2.65875,'x1':1.55125,'x2':-.43375,'x3':.10625,
                'x1:x2':.06375,'x1:x3':.12375,'x2:x3':.14875}
    assert {term:values['nist_coef:'+term] for term in expected} == pytest.approx(expected,abs=1e-12)
    assert fit['error_df'] == 1
    assert 'low power' in fit['inference']


@pytest.mark.skipif(shutil.which('Rscript') is None, reason='Optional independent reference requires base R')
def test_live_r_reference_examples(tmp_path):
    report = reference.run(tmp_path/'reference')
    assert report['status'] == 'pass'
    assert len(report['checks']) == 90
    assert all(row['passed'] for row in report['checks'])


@pytest.mark.skipif(shutil.which('Rscript') is None, reason='Optional independent reference requires base R')
def test_missing_engine_reference_key_cannot_pass(tmp_path,monkeypatch):
    values,fit=reference.calculate()
    values.pop('nist_coef:x1:x2')
    monkeypatch.setattr(reference,'calculate',lambda:(values,fit))
    with pytest.raises(ValueError,match='same 90 named calculations'):
        reference.run(tmp_path/'missing-check')
