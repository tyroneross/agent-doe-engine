#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Tyrone Ross, Jr
# SPDX-License-Identifier: Apache-2.0
"""Paired item analysis. The declared unit, not timing repeats, determines n."""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
import statistics
import sys
from doe_stats import t_ppf
from objectives import guard_status


def analyze_pairs(rows, *, baseline='baseline', candidate='candidate', response='continuous', direction='higher', margin=0.0, alpha=0.05):
    if baseline == candidate or response not in ('continuous', 'binary') or direction not in ('higher', 'lower'):
        raise ValueError('Use distinct arms, binary/continuous response, and higher/lower direction')
    if isinstance(alpha, bool) or not 0 < alpha < 1 or not math.isfinite(alpha):
        raise ValueError('alpha must be between zero and one')
    if isinstance(margin, bool) or not math.isfinite(margin) or margin < 0:
        raise ValueError('margin must be finite and nonnegative')
    arms = {baseline: {}, candidate: {}}
    seen = set()
    for r in rows:
        if not isinstance(r, dict) or r.get('arm') not in arms:
            raise ValueError('Each observation must name one of the two declared arms')
        unit = r.get('unit_id')
        if not isinstance(unit, str) or not unit.strip():
            raise ValueError('Each observation requires a nonempty unit_id')
        if guard_status(dict(r, guard_ok=r.get('guard_ok'))) is not True:
            raise ValueError('Failed or unknown guards cannot enter paired inference')
        v = r.get('value')
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            raise ValueError('Each value must be a finite number')
        if response == 'binary' and v not in (0, 1):
            raise ValueError('Binary observations must be zero or one')
        rep = r.get('replicate_id')
        if rep is not None and (not isinstance(rep, str) or not rep.strip()):
            raise ValueError('replicate_id must be a nonempty string')
        key = (r['arm'], unit, rep)
        if key in seen:
            raise ValueError('Duplicate observation identity; assign distinct replicate_id values')
        seen.add(key)
        samples = arms[r['arm']].setdefault(unit, [])
        if samples and (response == 'binary' or rep is None or (r['arm'], unit, None) in seen):
            raise ValueError('Repeated continuous measurements need replicate IDs; binary items must be unique')
        samples.append(float(v))
    if not arms[baseline] or arms[baseline].keys() != arms[candidate].keys():
        raise ValueError('Both arms need the same nonempty set of independent units; no silent dropping')
    units = sorted(arms[baseline])
    b = [statistics.mean(arms[baseline][u]) for u in units]
    c = [statistics.mean(arms[candidate][u]) for u in units]
    sign = 1 if direction == 'higher' else -1
    differences = [sign * (cv - bv) for bv, cv in zip(b, c)]
    mean = statistics.mean(differences)
    out = dict(method='paired unit means', independent_unit='unit_id (caller-declared)',
               n_units=len(units), n_observations=len(rows), baseline=baseline, candidate=candidate,
               baseline_mean=statistics.mean(b), candidate_mean=statistics.mean(c),
               direction=direction, improvement=mean, practical_margin=margin, alpha=alpha,
               interval=None, supports_practical_improvement=False, promotion_ready=False,
               limitations=['Unit independence and scorer validity must be established separately.',
                            'This comparison does not correct for adaptive selection or multiple comparisons.'])
    if response == 'binary':
        bc = sum(bv == 1 and cv == 0 for bv, cv in zip(b,c))
        cb = sum(bv == 0 and cv == 1 for bv, cv in zip(b,c))
        n = bc + cb
        p = min(1.0, 2 * sum(math.comb(n, j) for j in range(min(bc,cb)+1)) / 2**n) if n else 1.0
        out.update(method='exact two-sided McNemar', baseline_only_correct=bc, candidate_only_correct=cb,
                   p_value=p, status='descriptive_binary_comparison')
        out['limitations'].append('McNemar tests equality; it does not certify a nonzero practical margin.')
    elif len(units) < 2:
        out.update(status='insufficient_independent_units')
    else:
        sd = statistics.stdev(differences)
        if sd == 0:
            out.update(status='zero_observed_variance')
            out['limitations'].append('No observed variance: an uncertainty interval is not justified by this sample.')
        else:
            radius = t_ppf(1-alpha/2, len(units)-1) * sd / math.sqrt(len(units))
            interval = [mean-radius, mean+radius]
            out.update(method='paired Student t interval on unit means', interval=interval,
                       supports_practical_improvement=interval[0] > margin,
                       status='supports_practical_improvement' if interval[0] > margin else 'inconclusive')
            out['limitations'].append('Student t interval assumes independent units and suitably distributed mean differences.')
    return out


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--observations', required=True, help='JSONL with arm, unit_id, value and optional replicate_id')
    p.add_argument('--baseline', default='baseline');p.add_argument('--candidate', default='candidate')
    p.add_argument('--response', choices=['continuous','binary'], default='continuous')
    p.add_argument('--direction', choices=['higher','lower'], default='higher')
    p.add_argument('--margin', type=float, default=0);p.add_argument('--alpha', type=float, default=.05)
    a=p.parse_args(argv)
    try:
        rows=[json.loads(s) for s in Path(a.observations).read_text().splitlines() if s.strip()]
        result=analyze_pairs(rows,baseline=a.baseline,candidate=a.candidate,response=a.response,direction=a.direction,margin=a.margin,alpha=a.alpha)
    except (ValueError, OSError, TypeError) as exc:
        print(json.dumps({'status':'invalid','error':str(exc)}),file=sys.stderr);return 2
    print(json.dumps(result,indent=2,allow_nan=False));return 0

if __name__ == '__main__':
    raise SystemExit(main())
