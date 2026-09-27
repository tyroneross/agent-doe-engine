#!/usr/bin/env python3
"""Compare engine calculations with independently executed base R examples."""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import numpy as np
from doe import fit_effects
from doe_stats import t_ppf
from paired_analysis import analyze_pairs
from objectives import desirability_run


def calculate():
    # NIST standard/Yates order differs from itertools' conventional order.
    design = np.array([[(-1, 1)[(i >> bit) & 1] for bit in range(3)] for i in range(8)])
    y = np.array([1.70, 4.57, .55, 3.39, 1.51, 4.59, .67, 4.29])
    fit = fit_effects(design, y, include_interactions=True)
    values = {'nist_coef:(Intercept)': fit['intercept']}
    for i, value in fit['main'].items(): values[f'nist_coef:x{i+1}'] = value
    for (i,j), value in fit['interactions'].items(): values[f'nist_coef:x{i+1}:x{j+1}'] = value
    stats = {'(Intercept)': fit['intercept_stats']}
    stats.update({f'x{i+1}':v for i,v in fit['main_stats'].items()})
    stats.update({f'x{i+1}:x{j+1}':v for (i,j),v in fit['inter_stats'].items()})
    for term, record in stats.items():
        for metric in ('se','p_value'):
            values[f'nist_{metric}:{term}'] = record[metric]
        for index, side in enumerate(('lower','upper')):
            values[f'nist_{side}:{term}'] = record['ci95'][index]
    baseline = [.7,-1.6,-.2,-1.2,-.1,3.4,3.7,.8,0,2]
    candidate = [1.9,.8,1.1,.1,-.1,4.4,5.5,1.6,4.6,3.4]
    rows = [{'unit_id':str(i),'arm':arm,'value':v,'guard_ok':True}
            for i,(b,c) in enumerate(zip(baseline,candidate)) for arm,v in [('baseline',b),('candidate',c)]]
    result = analyze_pairs(rows)
    values.update({'sleep:mean':result['improvement'],'sleep:lower':result['interval'][0],'sleep:upper':result['interval'][1]})
    for df in (1,2,5,9,30,100):
        for p in (.025,.5,.95,.975,.995): values[f'qt:{df}:{p:g}'] = t_ppf(p,df)
    for b in (0,1,3,10):
        for c in (0,2,7,12):
            if b+c == 0: continue
            rows = [{'unit_id':str(i),'arm':arm,'value':v,'guard_ok':True} for i in range(b+c)
                    for arm,v in [('baseline',int(i<b)),('candidate',int(i>=b))]]
            values[f'exact:{b}:{c}'] = analyze_pairs(rows,response='binary')['p_value']
    for x in (75,80,81.09,90,97,100):
        obj = [{'name':'conversion','direction':'higher','weight':1,'min_acceptable':80,'target':97}]
        values[f'dmax:{x:g}'] = desirability_run({'conversion':x},obj,{'conversion':{'min':75,'max':100}})
    values['desirability:weighted'] = desirability_run({'a':2.5,'b':4},[
        {'name':'a','direction':'lower','weight':2},{'name':'b','direction':'higher','weight':1}],
        {'a':{'min':0,'max':10},'b':{'min':0,'max':10}})
    return values, fit


def run(output):
    output = Path(output)
    if output.exists(): raise ValueError('Use a new output directory to preserve prior evidence')
    output.mkdir(parents=True)
    reference = output/'r-reference.csv'
    subprocess.run(['Rscript','--vanilla',str(Path(__file__).with_name('reference.R')),str(reference)],check=True)
    expected = {r['check']:float(r['expected']) for r in csv.DictReader(reference.open())}
    actual,fit = calculate()
    if set(actual) != set(expected) or len(expected) != 90:
        raise ValueError('Reference coverage differs: require exactly the same 90 named calculations')
    rows = [{'check':k,'engine':v,'reference':expected[k],'absolute_error':abs(v-expected[k]),
             'passed':math.isclose(v,expected[k],rel_tol=1e-8,abs_tol=1e-10)} for k,v in actual.items()]
    report = {'status':'pass' if all(r['passed'] for r in rows) else 'fail','checks':rows,
              'reference_runtime':Path(str(reference)+'.version.txt').read_text().strip(),
              'source_sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in [Path(__file__),Path(__file__).with_name('reference.R'),ROOT/'scripts/doe.py',ROOT/'scripts/doe_stats.py',ROOT/'scripts/paired_analysis.py',ROOT/'scripts/objectives.py']},
              'scope':'Arithmetic checks on known examples, not assumptions, power, goal validity or production effectiveness.'}
    (output/'comparison.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    with (output/'comparison.csv').open('w') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    print(json.dumps({'status':report['status'],'checks':len(rows),'max_absolute_error':max(r['absolute_error'] for r in rows),'runtime':report['reference_runtime']}))
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True)
    sys.exit(0 if run(p.parse_args().output)['status']=='pass' else 1)
