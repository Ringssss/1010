"""Independent raw single-node ownership, budget, and protected asset audit."""
import argparse
import json
from pathlib import Path

from audit_core import Audit


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--actual',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();audit=Audit();actual=args.actual.resolve()
    summary=audit.read(actual/'summary.json');audit.reference(summary['plan_ref'])
    plan=audit.read(summary['plan_ref']['path'])
    audit.check('one completed physical session, one supervisor, one successful stop/collection',
        summary['status']=='completed_pending_independent_review' and summary['GPU_launches']==1
        and len(summary['workers'])==len(summary['all_stop'])==len(plan['processes'])==1
        and all(x.get('returncode')==0 for x in summary['workers'].values())
        and all(x.get('returncode')==0 for x in summary['all_stop'].values())
        and summary['collection']=={'node27':{'passed':True}})
    before=audit.read(actual/'baseline_node27/stdout.json')
    after=audit.read(actual/'after_stop_node27/stdout.json')
    spec=audit.read(Path(plan['setup_directory'])/'node27.setup.spec.json')
    audit.check('original exact container incarnation preserved and quiet',before['container']['Id']
        ==after['container']['Id']==spec['container_id'] and before['container']['State']['StartedAt']
        ==after['container']['State']['StartedAt']==spec['container_started_at']
        and after['cgroup']['files']['pids.current']=='1' and all(after['ports_free'].values())
        and '29981' in {str(x) for x in after['ports_free']})
    def owners(body):
        return {r['pid']:tuple(r[k] for k in ('pid','start_ticks','cgroup','argv_sha256')) for r in body['protected_owners']}
    def containers(body):
        return {r['id']:tuple(r[k] for k in ('id','name','StartedAt','Running','Pid')) for r in body['protected_containers']}
    audit.check('old owners/containers/original XML unchanged',owners(before)==owners(after)
        and containers(before)==containers(after) and before['original_xml_sha256']==after['original_xml_sha256'])
    for asset in spec['assets']:
        audit.check('original/new deployed source SHA unchanged: '+asset['relative_path'],
            before['source_assets'].get(asset['relative_path'])==after['source_assets'].get(asset['relative_path'])==asset['sha256'])
    collected=audit.read(actual/'exact_collect_node27/stdout.json')
    audit.check('precise own child PID/group absent; collector issued no signals',collected['passed']
        and collected['signals']==[] and all(x['passed'] and not x['current']['live'] and not x['group'] for x in collected['reports']))
    process=plan['processes'][0]
    supervisor=audit.read(actual/'artifacts_node27/raw'/Path(process['out']).name/'supervisor.summary.json')
    audit.check('exact supervisor TERM and successful owned group cleanup',supervisor['passed']
        and supervisor['plan_sha256']==summary['plan_ref']['sha256'] and supervisor['stop_reason']=='owned_stop_marker'
        and supervisor['child_returncode']==-15 and supervisor['cleanup']['passed']
        and supervisor['cleanup']['signals']==['TERM'] and not supervisor['cleanup']['after']['live']
        and not supervisor['cleanup']['pending_group_members'])
    budget=audit.read(actual/'budget_evidence.json')
    monitor=audit.read(actual/'budget_monitor.json') if (actual/'budget_monitor.json').exists() else []
    end_budget=audit.read(actual/'after_measure_budget.json')
    bundle=audit.read(actual/'measure/stage_measurements.json')
    max_dram=0;max_hbm=0
    groups=[budget['nodes']]+[x['nodes'] for x in monitor]+[end_budget]
    for row in bundle['samples']:
        raw=audit.read(row['raw_refs'][0]['path']);groups.append(raw['gate'])
    for index,group in enumerate(groups):
        data=group['node27'];max_dram=max(max_dram,data['dram_current_bytes']);max_hbm=max(max_hbm,data['hbm_absolute_upper_bytes'])
        audit.check('observed runtime-inclusive budget sample '+str(index),data['dram_current_bytes']<=6 << 30
            and data['hbm_absolute_upper_bytes']+data['planned_incremental_upper_bytes']+(256 << 20)<=2 << 30)
    audit.check('fresh physical DRAM reserve',budget['nodes']['node27']['physical_dram_available_bytes'] >= (2 << 30)+(256 << 20))
    for sample in supervisor['samples']:
        max_dram=max(max_dram,int(sample['memory.current']))
    audit.check('supervisor DRAM observed maximum within cap',max_dram<=6 << 30)
    result=audit.save(args.output,'Physical single-node sampled resources, exact owned process cleanup, original owner/container/XML/source preservation',
        actual_session=plan['session'],calibration_bundle_ref=next(x for x in audit.refs if x['path']==str(actual/'measure/stage_measurements.json')),
        sampled_max_dram_bytes=max_dram,sampled_max_hbm_absolute_upper_bytes=max_hbm,
        continuous_peak_measured=False,protected_owner_count=len(owners(before)),protected_container_count=len(containers(before)))
    print(json.dumps({'passed':result['passed'],'checks':len(audit.checks),'failed':[x['name'] for x in audit.checks if not x['passed']]}))
    if not result['passed']:raise SystemExit(1)


if __name__=='__main__':main()
