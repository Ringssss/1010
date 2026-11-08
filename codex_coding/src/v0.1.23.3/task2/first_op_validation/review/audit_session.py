"""Read-only review of local-collected resource protection and precise cleanup."""
import argparse
import hashlib
import json
from pathlib import Path


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--actual',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    args.actual=args.actual.resolve()
    args.output.mkdir(parents=True,exist_ok=False)
    checks,refs=[],[]
    def read(path):
        refs.append({'path':str(path.resolve()),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
        return json.loads(path.read_text())
    def check(name,passed):
        checks.append({'name':name,'passed':bool(passed)})
    summary=read(args.actual/'summary.json')
    plan=read(Path(summary['plan_ref']['path']))
    check('actual exact frozen plan reference',refs[-1]['sha256']==summary['plan_ref']['sha256'])
    check('one completed session and exactly nine stops/supervisors',summary['status']=='completed_pending_independent_review'
          and len(summary['all_stop'])==len(summary['workers'])==9
          and all(r['returncode']==0 for r in summary['all_stop'].values())
          and all(r['returncode']==0 for r in summary['workers'].values())
          and all(v['passed'] for v in summary['collection'].values()))
    final=read(args.actual/'measure/final_inventory.json')
    check('all Catalog/used/transactions/SSD empty',not final['replicas']
          and all(not w['transactions'] and not any(w['used_bytes'].values()) for w in final['workers'])
          and all(v['files']==0 for v in final['ssd'].values()))
    measurements=read(args.actual/'measure/stage_measurements.json')
    measure_summary=read(args.actual/'measure/summary.json')
    check('exact8 requests/16copies/8 interventions ledgers',measure_summary['GPU_measurements']==measure_summary['requests']==8
          and measure_summary['measurement_copy_bytes']==128<<20 and measure_summary['intervention_copy_bytes']==64<<20
          and measure_summary['logical_SHA_bytes']==64<<20 and measure_summary['bootstrap_bytes']==16<<20
          and measure_summary['further_GPU_requests']==measure_summary['model_updates']==0)
    source=read(args.actual/'measure/source_lineage.json')
    bootstrap=read(args.actual/'measure/bootstrap0.json')
    old_bootstrap=read(Path(source['old_bootstrap_ref']['path']))
    check('old source bootstrap exact SHA retained', refs[-1]['sha256']==source['old_bootstrap_ref']['sha256'])
    def sha_rows(flow):
        return sorted((v['source_sha256'],v['target_sha256'],v.get('n',v.get('bytes'))) for v in flow['verification']['operations'].values())
    check('new source all16MiB SHA matches original',source['full16MiB_SHA_equal']
          and bootstrap['verification']['passed'] and bootstrap['verification']['checked_bytes']==16<<20
          and sha_rows(bootstrap)==sha_rows(old_bootstrap))
    owners,containers=0,0
    max_dram,max_hbm={},{}
    budget=read(args.actual/'budget_evidence.json')
    monitor=read(args.actual/'budget_monitor.json')
    for sample in [budget['nodes']]+[s['nodes'] for s in monitor]+[r['gate'] for r in measurements['requests']]:
        for node,evidence in sample.items():
            check(node+': recorded DRAM/HIP budget sample accepted',evidence['dram_current_bytes']<=6<<30
                  and evidence['hbm_absolute_upper_bytes']<=2<<30
                  and evidence['hbm_absolute_upper_bytes']+evidence['planned_incremental_upper_bytes']<=2<<30)
            max_dram[node]=max(max_dram.get(node,0),evidence['dram_current_bytes'])
            max_hbm[node]=max(max_hbm.get(node,0),evidence['hbm_absolute_upper_bytes'])
    for node in ('node28','node30','node27'):
        before=read(args.actual/('baseline_'+node)/'stdout.json')
        after=read(args.actual/('after_stop_'+node)/'stdout.json')
        spec=read(Path(plan['setup_directory'])/(node+'.setup.spec.json'))
        check(node+': selected container unchanged and quiet',before['container']['Id']==after['container']['Id']==spec['container_id']
              and before['container']['State']['StartedAt']==after['container']['State']['StartedAt']==spec['container_started_at']
              and after['cgroup']['files']['pids.current']=='1' and all(after['ports_free'].values()))
        owner_fields=('pid','start_ticks','cgroup','argv_sha256')
        def owner_map(body):return {r['pid']:tuple(r[k] for k in owner_fields) for r in body['protected_owners']}
        def container_map(body):return {r['id']:tuple(r[k] for k in ('id','name','StartedAt','Running','Pid')) for r in body['protected_containers']}
        check(node+': protected original owners/containers/XML unchanged',owner_map(before)==owner_map(after)
              and container_map(before)==container_map(after) and before['original_xml_sha256']==after['original_xml_sha256'])
        owners+=len(before['protected_owners']);containers+=len(before['protected_containers'])
        check(node+': full frozen source closure unchanged before/after',all(
            before['source_assets'].get(r['relative_path'])==after['source_assets'].get(r['relative_path'])==r['sha256'] for r in spec['assets']))
        collected=read(args.actual/('exact_collect_'+node)/'stdout.json')
        check(node+': precise child collection no live PID/group/signals',collected['passed'] and not collected['signals']
              and all(r['passed'] and not r['current']['live'] and not r['group'] for r in collected['reports']))
        for proc in [r for r in plan['processes'] if r['node']==node]:
            path=args.actual/('artifacts_'+node)/'raw'/Path(proc['out']).name/'supervisor.summary.json'
            own=read(path)
            check(proc['key']+': supervisor exact planned TERM/child exit/group empty',own['passed']
                  and own['plan_sha256']==summary['plan_ref']['sha256'] and own['stop_reason']=='owned_stop_marker'
                  and own['child_returncode']==-15 and own['cleanup']['passed'] and own['cleanup']['signals']==['TERM']
                  and not own['cleanup']['after']['live'] and not own['cleanup']['pending_group_members'])
            # These processes share one cgroup per node; take a maximum, never sum duplicate samples.
            max_dram[node]=max(max_dram.get(node,0),*(int(x['memory.current']) for x in own['samples']))
    check('protected aggregate original13 owners/12 containers',owners==13 and containers==12)
    check('all supervisor DRAM samples within6GiB',all(v<=6<<30 for v in max_dram.values()))
    result={'status':'actual_session_resources_cleanup_passed' if all(c['passed'] for c in checks) else 'actual_session_resources_cleanup_failed',
            'checks':checks,'refs':refs,'sampled_max_dram_bytes':max_dram,'sampled_max_hbm_absolute_upper_bytes':max_hbm,
            'continuous_peak_measured':False,'GPU_launches':0,'remote_calls':0,'protected_owner_count':owners,'protected_container_count':containers}
    (args.output/'qualification.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'status':result['status'],'checks':len(checks),'failed':[c['name'] for c in checks if not c['passed']],
                      'DRAM':max_dram,'HBM':max_hbm}))
    if any(not c['passed'] for c in checks):raise SystemExit(1)


if __name__=='__main__':main()
