"""Audit a stopped physical admission from collected evidence only."""
import argparse
import hashlib
import json
from pathlib import Path

from audit_core import Audit


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--actual',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();actual=args.actual.resolve();audit=Audit()
    summary=audit.read(actual/'summary.json');audit.reference(summary['plan_ref'])
    plan=audit.read(summary['plan_ref']['path'])
    audit.check('one failed resource admission, exactly one owned worker, no calibration bundle',summary['status']=='failed'
        and summary['GPU_launches']==1 and len(summary['workers'])==len(summary['all_stop'])==len(plan['processes'])==1
        and not (actual/'measure/stage_measurements.json').exists() and not (actual/'measure/bootstrap.json').exists())
    stderr_path=actual/'mesh_controller/stderr.txt';stderr=stderr_path.read_text()
    audit.refs.append({'path':str(stderr_path),'sha256':hashlib.sha256(stderr_path.read_bytes()).hexdigest()})
    audit.check('failure specifically identified as postmesh HBM cap gate',
        'budget_snapshot(workers,setup)' in stderr and 'HBM absolute task cap/reserve/margin exhausted' in stderr)
    startup=audit.read(actual/'startup.inventory.json')[0]
    final=audit.read(actual/'mesh/final_inventory.json');postmesh=final['workers'][0]
    amounts={}
    for label,row in [('startup',startup),('postmesh',postmesh)]:
        upper=row['hbm_total_bytes']-row['headroom_bytes']['hbm']
        amounts[label]={'total_bytes':row['hbm_total_bytes'],'free_bytes':row['headroom_bytes']['hbm'],
            'same_HIP_absolute_upper_bytes':upper,'same_HIP_absolute_upper_GiB':upper/(1 << 30),
            'torch_allocated_bytes':row['hbm_allocated_bytes'],'torch_reserved_bytes':row['hbm_reserved_bytes'],
            'cgroup_memory_current_bytes':row['runtime_identity']['cgroup_memory_current_bytes']}
        audit.check(label+': recorded conservative HBM upper fails 2GiB cap even before reserve',upper>2 << 30)
    audit.check('same worker epoch/BDF; empty owned Catalog and transaction state',startup['epoch']==postmesh['epoch']
        and startup['runtime_identity']['actual_bdf']==postmesh['runtime_identity']['actual_bdf']=='0000:09:00.0'
        and final['replicas']==[] and final['ssd']=={} and not postmesh['transactions'] and not any(postmesh['used_bytes'].values()))
    audit.check('torch 64MiB postmesh staging is separate allocator evidence, not task runtime upper',
        startup['hbm_allocated_bytes']==startup['hbm_reserved_bytes']==0
        and postmesh['hbm_allocated_bytes']==postmesh['hbm_reserved_bytes']==postmesh['staging_bytes']==64 << 20)
    before=audit.read(actual/'baseline_node27/stdout.json');after=audit.read(actual/'after_stop_node27/stdout.json')
    spec=audit.read(Path(plan['setup_directory'])/'node27.setup.spec.json')
    prior=audit.read(Path(plan['setup_directory'])/'node27.prior.spec.json')
    def owners(body):return {x['pid']:tuple(x[k] for k in ('pid','start_ticks','cgroup','argv_sha256')) for x in body['protected_owners']}
    def containers(body):return {x['id']:tuple(x[k] for k in ('id','name','StartedAt','Running','Pid')) for x in body['protected_containers']}
    audit.check('original owner/container/XML identities preserved',owners(before)==owners(after)
        and containers(before)==containers(after) and before['original_xml_sha256']==after['original_xml_sha256'])
    audit.check('exact selected container quiet, new master port free, no OOM',before['container']['Id']==after['container']['Id']==spec['container_id']
        and before['container']['State']['StartedAt']==after['container']['State']['StartedAt']==spec['container_started_at']
        and after['cgroup']['files']['pids.current']=='1' and all(after['ports_free'].values())
        and '29981' in {str(k) for k in after['ports_free']} and 'oom 0' in after['cgroup']['files']['memory.events']
        and 'oom_kill 0' in after['cgroup']['files']['memory.events'])
    for asset in spec['assets']:
        audit.check('deployed source protected: '+asset['relative_path'],before['source_assets'].get(asset['relative_path'])
            ==after['source_assets'].get(asset['relative_path'])==asset['sha256'])
    current={x['relative_path']:x['sha256'] for x in spec['assets']}
    audit.check('all prior135 asset versions retained',len(prior['assets'])==135
        and all(current.get(x['relative_path'])==x['sha256'] for x in prior['assets']))
    collect=audit.read(actual/'exact_collect_node27/stdout.json')
    audit.check('precise own child/group absent, collector issued no signals',collect['passed'] and not collect['signals']
        and all(x['passed'] and not x['current']['live'] and not x['group'] for x in collect['reports']))
    process=plan['processes'][0]
    supervisor=audit.read(actual/'artifacts_node27/raw'/Path(process['out']).name/'supervisor.summary.json')
    audit.check('supervisor exact plan/TERM/-15 and no live process-group members',supervisor['passed']
        and supervisor['plan_sha256']==summary['plan_ref']['sha256'] and supervisor['stop_reason']=='owned_stop_marker'
        and supervisor['child_returncode']==-15 and supervisor['cleanup']['passed'] and supervisor['cleanup']['signals']==['TERM']
        and not supervisor['cleanup']['after']['live'] and not supervisor['cleanup']['pending_group_members'])
    audit.check('all stop/supervisor/collection commands succeeded',all(x.get('returncode')==0 for x in summary['workers'].values())
        and all(x.get('returncode')==0 for x in summary['all_stop'].values()) and summary['collection']=={'node27':{'passed':True}})
    max_dram=max(int(x['memory.current']) for x in supervisor['samples'])
    audit.check('supervisor DRAM samples within6GiB',max_dram<=6 << 30)
    result=audit.save(args.output,'Independent evidence review of physical HBM admission failure and exact cleanup; no GPU/SSH probes',
        status='resource_admission_failed_cleanup_verified',actual_session=plan['session'],amounts=amounts,
        calibration_requests=0,bootstrap_requests=0,model_identified=False,C2_status='skipped_resource_admission_failed',
        physical_admission=False,sampled_max_dram_bytes=max_dram,continuous_peak_measured=False,
        protected_owner_count=len(owners(before)),protected_container_count=len(containers(before)),
        prior_assets=len(prior['assets']),deployed_assets=len(spec['assets']),
        attribution='same-HIP global used-memory upper; task-only runtime and other process attribution unknown')
    print(json.dumps({'passed':result['passed'],'checks':len(audit.checks),'amounts':amounts,
        'failed':[x['name'] for x in audit.checks if not x['passed']]}))
    if not result['passed']:raise SystemExit(1)


if __name__=='__main__':main()
