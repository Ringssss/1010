#!/usr/bin/env python3
"""Register one frozen restore binding and CPU plan; never contact live workers."""
import argparse
import copy
import json
from pathlib import Path
import sys

HERE = Path(__file__).parent
ROOT = HERE.parents[5]
sys.path.insert(0,str(ROOT/'main_lib'))
from fluidckpt.benchmarks.basic_scenarios import build_method_plan
from fluidckpt.benchmarks.topology_cases import canonical_hash, interleave, validate_case
from freeze_case import digest, write_json


def verify_freeze(manifest_path):
    """Bind the registration to untouched evidence, code, and immutable config."""
    manifest = json.loads(manifest_path.read_text())
    if (manifest.get('schema_version')!='topology_freeze.v1'
            or manifest.get('development')!='v0.1.23.3/task2'
            or not manifest.get('cases') or manifest.get('new_GPU_samples')!=0):
        raise ValueError('freeze manifest schema/development mismatch')
    payload = copy.deepcopy(manifest)
    expected = payload.pop('manifest_sha256')
    if canonical_hash(payload) != expected:
        raise ValueError('manifest canonical hash mismatch')
    directory = manifest_path.parent
    for name, row in manifest['inputs'].items():
        if digest(Path(row['path'])) != row['sha256']:
            raise ValueError('live input hash changed: '+name)
        if digest(directory/'inputs'/(name+'.json')) != row['sha256']:
            raise ValueError('frozen input hash changed: '+name)
    for path, expected in manifest['code_sha256'].items():
        if digest(ROOT/path) != expected:
            raise ValueError('planner/executor code changed: '+path)
    for row in manifest['cases']:
        case=json.loads((directory/'cases'/(row['case_id']+'.json')).read_text())
        validate_case(case)
        if canonical_hash(case)!=row['case_sha256']:
            raise ValueError('case hash changed: '+row['case_id'])
    return manifest


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',type=Path,required=True)
    p.add_argument('--case',required=True)
    p.add_argument('--layout',help='default: first frozen fixed layout')
    p.add_argument('--scenario',help='default: first mutually exclusive restore scenario')
    p.add_argument('--method',choices=('janus_host','fastest','resource_lp'),default='fastest')
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    manifest=verify_freeze(args.manifest)
    directory=args.manifest.parent
    entry=next((r for r in manifest['cases'] if r['case_id']==args.case),None)
    if not entry:
        p.error('unknown frozen case')
    case=json.loads((directory/'cases'/(args.case+'.json')).read_text())
    adapter=json.loads((directory/'adapter_inputs'/(args.case+'.json')).read_text())
    # Adapter files are derived views, so check every nontrivial field against frozen config.
    for key, expected in [('base',case['base']),('candidates',case['candidates']),
                          ('scenarios',case['restore_scenarios']),('workers',case['workers']),
                          ('tier_budgets',case['budget']['tier_budgets']),
                          ('location_budgets',case['budget']['location_budgets']),('objective',case['objective'])]:
        if adapter[key]!=expected:
            raise ValueError('adapter config differs from frozen case: '+key)
    session=json.loads((directory/'inputs/session.json').read_text())
    if entry['profile_source']=='functional_provisional_assumption':
        if adapter['profile']!=session['profile']:
            raise ValueError('adapter provisional profile changed')
    else:
        calibration=json.loads((directory/'inputs/calibration.json').read_text())
        if adapter['profile']!=calibration['application_profile']['profile']:
            raise ValueError('adapter calibrated profile changed')
    layout=next((l for l in case['fixed_layouts'] if l['name']==(args.layout or case['fixed_layouts'][0]['name'])),None)
    scenario=next((s for s in case['restore_scenarios'] if s['name']==(args.scenario or case['restore_scenarios'][0]['name'])),None)
    if layout is None or scenario is None:
        p.error('layout/scenario must exist in frozen case')
    by_id={c['id']:c for c in case['candidates']}
    replicas=copy.deepcopy(case['base'])+[copy.deepcopy(by_id[i]) for i in layout['candidate_ids']]
    for r in replicas:
        r.update(source_role='base' if r['tier']=='dram' else 'extra',allocation_id=r['id'])
    budget=case['budget']
    basic=dict(scenario_id='S4',workers=case['workers'],replicas=replicas,
               targets=scenario['targets'],profile=adapter['profile'],executor='serial',
               format_contract=case['format_contract'],budget=dict(
                   base_dram_bytes=budget['base_dram_bytes'],extra_hbm_bytes=budget['tier_budgets']['hbm'],
                   location_extra_hbm_bytes=budget['location_budgets'],
                   target_allocation_bytes=budget['target_allocation_bytes'],staging_bytes=budget['staging_bytes']))
    assessment=build_method_plan(basic,args.method)
    if assessment.get('plan'):
        assessment['plan']=interleave(assessment['plan'])
    binding=dict(schema_version='topology_registration.v1',status='CPU_registered_only',
                 frozen_manifest_sha256=manifest['manifest_sha256'],case_sha256=entry['case_sha256'],
                 case_id=args.case,layout=layout['name'],scenario=scenario['name'],method=args.method,
                 profile_source=entry['profile_source'],readiness=entry['readiness'],
                 candidate_state='hypothetical; runtime must build and rebind IDs/epochs before reuse',
                 runtime_slot=session['slots'][scenario['name']],
                 runtime_adapter_changes=(['accept frozen rank_interleaved common schedule'] if args.case=='T1_simple_control' else
                                          ['accept frozen layout/per-location caps instead of hardcoded4+4',
                                           'bind one mutually exclusive target slot per run',
                                           'accept frozen rank_interleaved common schedule']),
                 full_model_gate='new owned run, current endpoint preflight, build fullSHA, content/output/cleanup and peak ledger',
                 new_GPU_samples=0,GPU_or_SSH_mutations=False)
    if args.output.exists():
        raise FileExistsError('new output directory required')
    args.output.mkdir(parents=True)
    write_json(args.output/'runtime_case_preview.json',basic)
    write_json(args.output/'method_plan_preview.json',assessment)
    write_json(args.output/'registration.json',binding)
    print(json.dumps({'status':'CPU_registered_only','case':args.case,'scenario':scenario['name'],
                      'method_status':assessment['status'],'output':str(args.output),'new_GPU_samples':0}))


if __name__=='__main__':
    main()
