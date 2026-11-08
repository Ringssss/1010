"""Independently audit byte coverage, role mapping and shared-resource structure."""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import jsonschema

ROOT = Path(__file__).resolve().parents[6]


def audit(d):
    checks = {}
    def check(name, condition):
        checks[name] = bool(condition)
    q = 4 << 20
    workers = {w['id']: w for w in d['workers']}
    locations = {l['id']: l for l in d['locations']}
    blocks = {b['id']: b for b in d['blocks']}
    candidates = {c['id']: c for c in d['candidates']}
    paths = {p['candidate']: p for p in d['supergraph']['paths']}
    resources = {r['id']: r for r in d['supergraph']['resources']}
    phases = {p['id']: p for p in d['phases']}
    check('all_entity_ids_unique', all(len(x) == len(d[k]) for x,k in [(workers,'workers'),(locations,'locations'),(blocks,'blocks'),(candidates,'candidates')]) and len(paths) == len(d['supergraph']['paths']))
    check('exact_3node_2gpu_roles', set(workers) == {f'w{i}' for i in range(6)} and all(workers[f'w{i}']['node'] == f'n{i//2}' and workers[f'w{i}']['gpu'] == f'g{i%2}' for i in range(6)))
    check('four_target_roles_helper_roles', {w['id']:w['target_rank'] for w in workers.values()} == {'w0':0,'w1':None,'w2':1,'w3':None,'w4':2,'w5':3})
    check('8blocks_fixed_4MiB', len(blocks)==8 and d['block_unit_bytes']==q and d['payload_bytes']==8*q and all(b['nbytes']==q for b in blocks.values()))
    for rank,worker in enumerate(['w0','w2','w4','w5']):
        spans = sorted((b['offset_bytes'], b['nbytes']) for b in blocks.values() if b['rank']==rank)
        check(f'rank{rank}/complete_contiguous_8MiB', spans==[(0,q),(q,q)] and all(b['target_worker']==worker for b in blocks.values() if b['rank']==rank))
    check('processed_snapshot_not_raw_checkpoint', d['snapshot']['source']=='already processed runtime-ready fixed TP4 bytes' and bool(d['snapshot']['cold_checkpoint_transform']) and not d['snapshot']['model_payload_materialized'] and d['snapshot']['actual_model_binding'] is None and all(b['identity']==d['snapshot']['identity'] and b['generation']==1 for b in blocks.values()))
    check('12_memory_3_ssd_locations', len(locations)==15 and Counter(l['tier'] for l in locations.values())=={'hbm':6,'dram':6,'ssd':3})
    check('all_104_content_location_opportunities', len(candidates)==104 and set(candidates)=={f'{b["id"]}@{l["id"]}' for b in blocks.values() for l in locations.values() if l['tier']!='ssd' or l['id']==b['authoritative_location']})
    fixed_by_loc = Counter()
    all_exact = True
    for c in candidates.values():
        b,l=blocks[c['block']],locations[c['location']]
        fixed=l['tier']=='ssd'
        all_exact &= c['nbytes']==b['nbytes'] and c['fixed']==fixed and c['pause_charge_bytes']==(0 if fixed else b['nbytes'])
        if fixed:
            fixed_by_loc[l['id']] += c['nbytes']
    ledger=d['retention_accounting']
    check('every_optional_replica_charged_full',all_exact)
    check('full_authoritative_SSD_4_2_2',dict(fixed_by_loc)=={'ssd/n0':4*q,'ssd/n1':2*q,'ssd/n2':2*q} and ledger['ssd_authoritative_bytes']==8*q and ledger['ssd_authoritative_bytes_by_location']==dict(fixed_by_loc))
    check('target_HBM_separate_8q',ledger['target_hbm_restore_bytes']==8*q and ledger['target_hbm_restore_bytes_by_worker']=={'w0':2*q,'w2':2*q,'w4':2*q,'w5':2*q} and ledger['target_hbm_charged_to_pause_budget'] is False)
    check('known_peak_is_not_full_lifetime_peak',all(ledger[k] is None for k in ('staging_bytes_by_worker','runtime_bytes_by_worker','layout_transition_peak_bytes','physical_peak_bytes')) and 'retention + target allocation' in ledger['peak_policy'] and ledger['historical_physical_dram_limit_bytes_per_node']==2<<30 and not ledger['symbolic_capacity_is_operational_quota'])
    for cid,p in paths.items():
        c=candidates[cid]; b=blocks[c['block']]; l=locations[c['location']]; t=workers[b['target_worker']]
        expected={f'worker_service/{l["worker"]}',f'worker_service/{t["id"]}',f'gpu_copy/{t["id"]}',f'pci/{t["node"]}'}
        if l['tier']=='hbm':
            expected|={f'gpu_copy/{l["worker"]}',f'pci/{l["node"]}'}
        else:
            expected.add(f'numa_dram/{l["node"]}/{l["numa"]}')
        if l['tier']=='ssd':
            expected|={f'ssd_queue/{l["node"]}',f'host_staging/{l["node"]}/{l["numa"]}'}
        remote=l['node']!=t['node']
        if remote:
            expected|={f'nic_out/{l["node"]}',f'nic_in/{t["node"]}','fabric/three_node'}
            if l['tier']!='hbm':
                expected|={f'gpu_copy/{l["worker"]}',f'pci/{l["node"]}',f'host_staging/{l["node"]}/{l["numa"]}'}
        if l['node']==t['node'] and l['numa']!=t['numa']:
            expected.add(f'numa_link/{l["node"]}')
        actual=[r['resource'] for r in p['resource_incidence']]
        check(f'path/{cid}/independent_resource_incidence',set(actual)==expected and len(actual)==len(expected) and all(r['coefficient']==1 for r in p['resource_incidence']) and expected<=set(resources) and p['source_worker']==l['worker'] and p['target_worker']==t['id'] and p['cross_node']==remote)
    summaries=[]
    for phase in phases.values():
        caps=phase['capacity_bytes']; usage=Counter(); tiers=Counter(); counts=Counter(); good=True
        for item in phase['witness']:
            c=candidates.get(item['candidate'])
            if c is None:
                good=False;continue
            counts[item['block']]+=1
            good &= c['block']==item['block'] and item['nbytes']==blocks[item['block']]['nbytes']
            tier=locations[c['location']]['tier'];tiers[tier]+=item['nbytes']
            if tier!='ssd':usage[c['location']]+=item['nbytes']
        totals={t:sum(v for l,v in caps.items() if locations[l]['tier']==t) for t in ('hbm','dram')}
        check(f'{phase["id"]}/exact_witness_coverage_capacity',good and counts=={b:1 for b in blocks} and all(n<=caps.get(l,-1) for l,n in usage.items()))
        check(f'{phase["id"]}/aggregate_capacity_independent',totals==phase['aggregate_capacity_bytes'] and set(caps)=={l['id'] for l in locations.values() if l['tier']!='ssd'} and all(type(v) is int and v>=0 for v in caps.values()))
        check(f'{phase["id"]}/all_paths_and_SSD_fallback_legal',set(phase['constraint_resource_incidence'])=={p['id'] for p in paths.values()} and phase['fallback_policy']=='every authoritative SSD candidate remains legal')
        expected_incidence={p['id']:[r['resource'] if phase['sharing_mode']=='shared_node_numa' or r['resource'].startswith('worker_service/') else f'isolated/{p["id"]}/{r["resource"]}' for r in p['resource_incidence']] for p in paths.values()}
        check(f'{phase["id"]}/private_vs_shared_alias_independently_rebuilt',phase['constraint_resource_incidence']==expected_incidence)
        summaries.append({'phase':phase['id'],'capacity':totals,'witness_tier_bytes':dict(tiers),'known_hbm_retention_plus_separate_target_bytes':8*q+tiers['hbm'],'SSD_source_bytes_necessary_lower_bound':max(0,8*q-totals['hbm']-totals['dram'])})
    check('five_phases_exact',set(phases)=={'C0','C1','C2a','C2b','C3'})
    check('C1_C2a_only_capacity_controls_sharing',phases['C1']['constraint_resource_incidence']==phases['C2a']['constraint_resource_incidence'])
    check('C2a_C2b_only_resource_merge',all(phases['C2a'][k]==phases['C2b'][k] for k in ('capacity_bytes','aggregate_capacity_bytes','witness','fallback_policy')) and phases['C2a']['constraint_resource_incidence']!=phases['C2b']['constraint_resource_incidence'])
    check('C2b_C3_only_DRAM_capacity',phases['C2b']['constraint_resource_incidence']==phases['C3']['constraint_resource_incidence'] and all(phases['C2b']['capacity_bytes'][l['id']]==phases['C3']['capacity_bytes'][l['id']] for l in locations.values() if l['tier']=='hbm'))
    phase_report={s['phase']:s for s in summaries}
    check('C0_HBM_full_witness',phase_report['C0']['witness_tier_bytes']=={'hbm':8*q})
    check('C1_HBM4q_DRAM_nonzero_necessary_for_SSD_free',phase_report['C1']['capacity']['hbm']==4*q and phase_report['C1']['witness_tier_bytes']=={'hbm':4*q,'dram':4*q})
    check('C3_at_least_q_SSD_is_capacity_proof_not_device_IO',phase_report['C3']['capacity']=={'hbm':4*q,'dram':3*q} and phase_report['C3']['SSD_source_bytes_necessary_lower_bound']==q and phase_report['C3']['witness_tier_bytes'].get('ssd')==q)
    check('cost_mapping_runtime_execution_remain_unknown',d['gpu_command'] is None and all(d['readiness'][k] is False for k in ('physical_ready','cost_ready','runtime_budget_ready','actual_model_ready','schedule_ready','performance_ready')) and all(p['schedule'] is None and p['predicted_ready_seconds'] is None and not p['selected_by_planner'] for p in phases.values()) and all(r['physical_identity'] is None and r['capacity_bytes_per_second'] is None for r in resources.values()) and all(p['physical_path_binding'] is None and p['rate_bytes_per_second'] is None for p in paths.values()))
    return checks,summaries


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--contract',required=True,type=Path);parser.add_argument('--schema',required=True,type=Path);parser.add_argument('--output',required=True,type=Path);args=parser.parse_args()
    d=json.loads(args.contract.read_text()); schema=json.loads(args.schema.read_text())
    checks,summaries=audit(d)
    schema_errors=list(jsonschema.Draft7Validator(schema).iter_errors(d))
    checks['schema_Draft7_independent']=not schema_errors
    frozen=deepcopy(d);declared=frozen.pop('contract_hash')
    actual=hashlib.sha256(json.dumps(frozen,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    checks['canonical_contract_hash_independent']=actual==declared
    mutations={}
    def negative(name,mutate):
        bad=deepcopy(d);mutate(bad)
        try:
            failure=[k for k,v in audit(bad)[0].items() if not v]
        except (KeyError,IndexError,TypeError) as e:
            failure=[type(e).__name__]
        mutations[name]={'rejected':bool(failure),'failed_checks':failure}
        checks[f'negative/{name}']=bool(failure)
    negative('coverage_gap',lambda x:x['blocks'][1].update(offset_bytes=1))
    negative('missing_authoritative_SSD',lambda x:x['candidates'].pop(next(i for i,c in enumerate(x['candidates']) if c['fixed'])))
    negative('free_optional_replica',lambda x:next(c for c in x['candidates'] if not c['fixed']).update(pause_charge_bytes=0))
    negative('overbudget_witness',lambda x:x['phases'][0]['capacity_bytes'].update({'hbm/w0':0}))
    negative('missing_GPU_copy_incidence',lambda x:x['supergraph']['paths'][0].update(resource_incidence=[r for r in x['supergraph']['paths'][0]['resource_incidence'] if not r['resource'].startswith('gpu_copy/')]))
    negative('split_shared_HCA_per_path',lambda x:x['phases'][3]['constraint_resource_incidence'].update({k:[f'{r}/fake-private/{k}' if r.startswith('nic_') else r for r in ids] for k,ids in x['phases'][3]['constraint_resource_incidence'].items()}))
    negative('third_node_GPU_role_missing',lambda x:x['workers'].pop())
    negative('false_physical_ready',lambda x:x['readiness'].update(physical_ready=True))
    negative('false_full_peak',lambda x:x['retention_accounting'].update(physical_peak_bytes=1))
    args.output.mkdir(parents=True,exist_ok=False)
    refs=[]
    for p in (args.contract,args.schema,Path(__file__)):
        refs.append({'path':str(p.resolve().relative_to(ROOT)),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
    report={'development':'v0.1.23.3/task2','qualification':'passed_independent_structural_review_physical_unadmitted' if all(checks.values()) else 'failed',
            'checks':checks,'checks_passed':sum(checks.values()),'checks_total':len(checks),'schema_errors':[str(e) for e in schema_errors],
            'negative_checks':mutations,'phase_bytes':summaries,'evidence_refs':refs,'method':'linear independent contract audit; no solver, scheduling or parameter search',
            'layer_decision_latency_demonstrated':False,'physical_ready':False,'full_model_binding_ready':False,'GPU_calls':0,'SSH_actions':0,'remote_writes':0,'signals':0}
    (args.output/'qualification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    (args.output/'review.source.py').write_bytes(Path(__file__).read_bytes())
    print(json.dumps({'output':str(args.output),'passed':sum(checks.values()),'total':len(checks),'failures':[k for k,v in checks.items() if not v]}))
    if not all(checks.values()):raise SystemExit(1)


if __name__=='__main__':main()
