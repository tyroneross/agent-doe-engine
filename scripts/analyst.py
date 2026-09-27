#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Tyrone Ross, Jr
# SPDX-License-Identifier: Apache-2.0
"""Check experiment readiness and proposed amendments; never execute changes."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
import objectives
from doe import validate_levels


def plan_hash(plan):
    return hashlib.sha256(json.dumps(plan,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def check_plan(plan):
    if not isinstance(plan,dict):
        raise ValueError('Plan must be an object')
    issues=[]
    for field in ('question','target_population','independent_unit','batch_id','scorer_id','fixture_id'):
        if not isinstance(plan.get(field),str) or not plan[field].strip():
            issues.append(f'{field}: specify a nonempty value')
    factors=plan.get('factors',[])
    if not isinstance(factors,list) or not factors:
        issues.append('factors: specify at least one factor');factors=[]
    try:
        validate_levels(factors)
    except ValueError as exc:
        issues.append(f'factors: {exc}')
    if plan.get('selection', 'scalarize') not in ('scalarize', 'desirability', 'pareto'):
        issues.append('selection: use scalarize, desirability or pareto')
    objs=plan.get('objectives')
    if not isinstance(objs,list) or not objs or not all(isinstance(o,dict) for o in objs):
        issues.append('objectives: specify an objective list');objs=[]
    else:
        contract=objectives.validate_objectives(objs)
        issues.extend(f'objectives: {x}' for x in contract.get('errors',[]))
        issues.extend(f'objectives: {x}' for x in contract.get('warnings',[]))
    if not any(o.get('role')=='primary' for o in objs):
        issues.append('objectives: declare a primary outcome')
    for o in objs:
        for field in ('name','unit','measurement_method'):
            if not isinstance(o.get(field),str) or not o[field].strip():
                issues.append(f"{o.get('name','objective')}: specify {field}")
        if o.get('role')!='quality' and (o.get('validity')!='validated' or not isinstance(o.get('validity_evidence'),str) or not o['validity_evidence'].strip()):
            issues.append(f"{o.get('name','objective')}: validate the measurement with evidence")
    for field in ('run_budget','confirmation_units'):
        if type(plan.get(field)) is not int or plan[field] < 1:
            issues.append(f'{field}: specify a positive integer')
    split=plan.get('split',{})
    if not isinstance(split,dict):split={}
    if split.get('independent') is not True or not isinstance(split.get('screening_id'),str) or not split.get('screening_id','').strip() or not isinstance(split.get('confirmation_id'),str) or not split.get('confirmation_id','').strip() or split.get('screening_id')==split.get('confirmation_id'):
        issues.append('split: reserve independently identified confirmation data/restarts')
    for field in ('randomization','blocking','stopping_rule','guard'):
        if not isinstance(plan.get(field),str) or not plan[field].strip():
            issues.append(f'{field}: specify the procedure (including no blocking with justification)')
    return {'schema_version':1,'plan_hash':plan_hash(plan),'ready_for_execution':not issues,
            'promotion_ready':False,'issues':issues,
            'next_action':'qualify_measurements_or_plan' if issues else 'freeze_plan_and_generate_design',
            'limitations':['Readiness checks required declarations; it does not authenticate evidence or prove adequate power.','The host analyst must justify sample size, interactions and the response model.']}


def check_amendment(previous, proposed, amendment):
    if not all(isinstance(x,dict) for x in (previous,proposed,amendment)):
        raise ValueError('Plans and amendment must be objects')
    issues=[]
    if amendment.get('previous_plan_hash')!=plan_hash(previous):issues.append('previous_plan_hash does not match the frozen plan')
    for field in ('reason','evidence','expected_information'):
        if not isinstance(amendment.get(field),str) or not amendment[field].strip():issues.append(f'{field} is required')
    changed=sorted(k for k in previous.keys()|proposed.keys() if previous.get(k)!=proposed.get(k))
    if not changed:issues.append('No plan change proposed')
    if previous.get('batch_id')==proposed.get('batch_id'):issues.append('Changes require a new batch_id; never rewrite an active randomized batch')
    reused=amendment.get('confirmation_used_for_tuning', False)
    if type(reused) is not bool:
        issues.append('confirmation_used_for_tuning must be a boolean')
    if reused is True:
        old_split=previous.get('split',{})
        new_split=proposed.get('split',{})
        old=old_split.get('confirmation_id') if isinstance(old_split,dict) else None
        new=new_split.get('confirmation_id') if isinstance(new_split,dict) else None
        if not new or old==new:
            issues.append('Confirmation used for tuning requires a fresh confirmation split')
    readiness=check_plan(proposed);issues.extend(readiness['issues'])
    return {'amendment_valid':not issues,'changed_fields':changed,'issues':issues,
            'previous_plan_hash':plan_hash(previous),'proposed_plan_hash':plan_hash(proposed),
            'requires_new_measurement_version':bool({'objectives','scorer_id','fixture_id'}&set(changed)),
            'promotion_ready':False}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='cmd',required=True)
    check=sub.add_parser('check');check.add_argument('--plan',required=True)
    amend=sub.add_parser('amendment');amend.add_argument('--previous',required=True);amend.add_argument('--proposed',required=True);amend.add_argument('--amendment',required=True)
    a=p.parse_args(argv)
    try:
        read=lambda path:json.loads(Path(path).read_text())
        r=check_plan(read(a.plan)) if a.cmd=='check' else check_amendment(read(a.previous),read(a.proposed),read(a.amendment))
        print(json.dumps(r,indent=2,allow_nan=False))
        return 0 if r.get('ready_for_execution',r.get('amendment_valid')) else 1
    except (ValueError,TypeError,OSError) as exc:
        print(json.dumps({'status':'invalid','error':str(exc)}),file=sys.stderr);return 2

if __name__=='__main__':raise SystemExit(main())
