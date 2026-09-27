#!/usr/bin/env python3
"""Generate a clearly labeled synthetic campaign and exercise public CLIs offline."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from experiment_ledger import init_campaign,append_attempt,append_decision
from report import load_report,render_html,render_csv,render_markdown


def build(out):
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    script=Path(__file__).resolve().parents[1]/'scripts/doe.py'
    def run(args):
        r=subprocess.run([sys.executable,str(script),*args],capture_output=True,text=True,check=True)
        return json.loads(r.stdout)
    def save(name,value):
        (out/name).write_text(json.dumps(value,indent=2)+'\n');return str(out/name)
    factors=[{'name':'workers','low':1,'high':4},{'name':'batch','low':8,'high':32}]
    d=run(['generate','--factors',json.dumps(factors),'--seed','7'])
    obj=[{'name':'latency_ms','role':'primary','driver':'synthetic response','direction':'lower','weight':1,'target':85,'validity':'validated'}]
    for row in d['runs']:
        row['candidate_id']='synthetic-'+str(row['_run_id'])
        row['config_hash']=hashlib.sha256(json.dumps(row['_factors'],sort_keys=True).encode()).hexdigest()
    dp=save('design.json',d);op=save('objectives.json',obj)
    ledger=out/'campaign.jsonl'
    init_campaign(ledger,{'campaign_id':'synthetic-example','title':'Synthetic DOE example — fixture data','plan':{'source':'Known response function; no live performance measurements','factors':factors}})
    results=[]
    for cell in d['run_order']:
        a,b=d['matrix'][cell];binding=d['runs'][cell]
        for repeat,noise in enumerate([-1,0,1]):
            row={'attempt_id':f'screen-{cell}-{repeat}','run_id':cell,'candidate_id':binding['candidate_id'],'config_hash':binding['config_hash'],'fixture_id':'synthetic-v1','scorer_id':'known-function-v1','split_id':'screen-v1','item_ids':[f'screen-{repeat}'],'guard_ok':cell!=3,'values':{'latency_ms':100-20*a+2*b+noise}}
            results.append(row)
            append_attempt(ledger,{'attempt_id':f'screen-{cell}-{repeat}','batch_id':'screen-1','cell_id':str(cell),'phase':'screen','settings':binding['_factors'],'metrics':row['values'],'guard_ok':row['guard_ok'],'change':'Planned factor combination; each cell starts at the same baseline','measurements':{'latency_ms':{'method':'Synthetic function 100 - 20*a + 2*b + repeat noise','unit':'synthetic ms','n':1}},'provenance':row})
    rp=out/'results.jsonl';rp.write_text(''.join(json.dumps(r)+'\n' for r in results))
    args=['--design',dp,'--results',str(rp),'--objectives',op]
    analysis=run(['analyze',*args]);save('analysis.json',analysis)
    best=analysis['best_run'];binding=d['runs'][best]
    append_decision(ledger,{'decision_id':'confirm-selected','attempt_ids':[r['attempt_id'] for r in load_report(ledger=ledger)['attempts']],'reason':'Selected feasible candidate; run independent synthetic confirmation','next_change':f'Freeze {binding["_factors"]}; use confirmation fixture items','evidence':'analysis.json'})
    conf=[]
    for i,noise in enumerate([-1,0,1]):
        row={'attempt_id':f'confirm-{i}','run_id':best,'candidate_id':binding['candidate_id'],'config_hash':binding['config_hash'],'fixture_id':'synthetic-v1','scorer_id':'known-function-v1','split_id':'confirm-v1','item_ids':[f'confirm-{i}'],'guard_ok':True,'values':{'latency_ms':78+noise}}
        conf.append(row)
        append_attempt(ledger,{'attempt_id':f'confirm-{i}','batch_id':'confirm-1','cell_id':str(best),'phase':'confirmation','settings':binding['_factors'],'metrics':row['values'],'guard_ok':True,'change':'Independent synthetic confirmation items','measurements':{'latency_ms':{'method':'Synthetic confirmation around known optimum','unit':'synthetic ms','n':1}},'provenance':row})
    cp=out/'confirmation.jsonl';cp.write_text(''.join(json.dumps(r)+'\n' for r in conf))
    contract={'schema_version':1,'candidate':{'run_id':best,'candidate_id':binding['candidate_id'],'config_hash':binding['config_hash']},'fixture_id':'synthetic-v1','scorer_id':'known-function-v1','objectives':obj,'selection':'scalarize','split':{'screening_id':'screen-v1','confirmation_id':'confirm-v1','independent':True},'measurement':{'valid':True,'evidence':'Synthetic oracle only; no production claim'},'review':{'approved':True,'reviewer':'synthetic-test-oracle','evidence':'Fixture attestation only; not a human review'}}
    contractp=save('promotion-contract.json',contract)
    confirmation=run(['confirm',*args,'--confirmation',str(cp),'--contract',contractp]);save('confirmation-result.json',confirmation)
    append_decision(ledger,{'decision_id':'fixture-complete','attempt_ids':[f'confirm-{i}' for i in range(3)],'reason':'Failed screening cell contaminates the fitted model; confirmation must remain blocked','next_change':'Investigate the guard failure and run a fresh frozen campaign with valid measurements','evidence':'confirmation-result.json'})
    model=load_report(ledger=ledger)
    for name,render in [('html',render_html),('csv',render_csv),('md',render_markdown)]:
        (out/f'report.{name}').write_text(render(model))
    return confirmation

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True)
    print(json.dumps(build(parser.parse_args().output),indent=2))
