"""Review native six-worker artifacts and preserve evidence snapshots locally."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
Q = 4 << 20
EXPECTED = [('node28',0,0),('node28',1,3),('node30',4,4),('node30',5,7),('node27',0,0),('node27',4,4)]
BDFS = ['0000:09:00.0','0000:36:00.0','0000:85:00.0','0000:b5:00.0','0000:09:00.0','0000:85:00.0']


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--run-dir',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    a.out.mkdir(parents=True,exist_ok=False)
    refs,checks=[],[]
    def load(path):
        path=path.resolve()
        raw=path.read_bytes()
        dest=a.out/'evidence'/path.relative_to(ROOT)
        dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_bytes(raw)
        refs.append(dict(source_path=str(path),path=str(dest),sha256=hashlib.sha256(raw).hexdigest()))
        return json.loads(raw)
    def check(name,ok):checks.append(dict(name=name,passed=bool(ok)))
    def transfer(name,row,nbytes):
        verification=row['verification']
        operations=verification['operations']
        check(name+'_full_SHA',verification['passed'] is True and verification['checked_bytes']==nbytes and
              sum(x['n'] for x in operations.values())==nbytes and
              all(len(x['source_sha256'])==64 and x['source_sha256']==x['target_sha256'] for x in operations.values()))
        timing=row['controller_timing']
        check(name+'_separate_monotonic_timestamps',
              timing['load_begin_ns']<=timing['all_targets_filled_ns']<=timing['all_targets_published_ns'] and
              bool(timing['clock_domain']) and row['executor']=='serial')
    summary=load(a.run_dir/'summary.json')
    setup=load(a.run_dir/'runtime_setup.json')
    mesh=load(a.run_dir/'mesh/mesh.json')
    budget=load(a.run_dir/'budget_evidence.json')
    qualification=load(a.run_dir/'qualification/qualification.json')
    cases=load(a.run_dir/'cases/summary.json')
    base=load(a.run_dir/'cases/base.json')
    final=load(a.run_dir/'cases/final_inventory.json')
    check('operations_completed',summary['status']=='completed_pending_independent_review' and summary['GPU_launches']==6 and
          len(summary['workers'])==9 and all(x['returncode']==0 for x in summary['workers'].values()) and
          all(x['passed'] for x in summary['collection'].values()))
    check('six_actual_worker_identities',len(mesh['workers'])==6 and all(
        (w['node'],w['gpu'],w['numa'])==EXPECTED[i] and w['worker']==i and w['session']==setup['session'] and
        w['runtime_identity']['actual_bdf']==BDFS[i] and w['runtime_identity']['pci_numa_node']==EXPECTED[i][2] and
        w['runtime_identity']['boot_id']==setup['workers'][i]['boot_id'] and
        w['runtime_identity']['start_ticks']==setup['workers'][i]['pid_start_ticks'] and w['staging_bytes']==64<<20
        for i,w in enumerate(mesh['workers'])))
    check('postwarm_accounting',budget['runtime_staging_inclusion']=='included' and budget['budget_accounting_complete'] is True and
          all(s['dram_current_bytes']+(256<<20)<=s['dram_limit_bytes']==6<<30 and
              s['hbm_increment_bytes']+(256<<20)<=s['hbm_limit_bytes']==2<<30 for s in budget['nodes'].values()))
    check('qualification_actual_complete',qualification['purpose']=='physical' and qualification['mode']=='bounded_qualification' and
          qualification['status']=='complete' and qualification['session']==setup['session'] and
          set(qualification['paths'])==set(mesh['required_paths']))
    for key,proof in qualification['paths'].items():
        transfer('path_'+key,proof['transfer'],Q)
        check('path_'+key+'_current_owners',proof['source_epoch']==mesh['workers'][proof['source_worker']]['epoch'] and
              proof['target_epoch']==mesh['workers'][proof['target_worker']]['epoch'])
    base_rows={r['id']:r for r in base['current_catalog']}
    distribution={n:sum(r['n'] for r in base_rows.values() if r['storage_node']==n) for n in ('node28','node30','node27')}
    check('complete_SSD32MiB_actual_16_8_8',len(base_rows)==8 and base['complete_bytes']==8*Q and
          list(distribution.values())==[4*Q,2*Q,2*Q] and
          all(r['tier']=='ssd' and r['state']=='VALID' and r['leases']==0 and r['n']==Q for r in base_rows.values()))
    check('synthetic_TP4_explicit',base['synthetic'] is True and all(
          m['meta']['synthetic'] is True and m['meta']['tp_rank']==int(rank) and m['meta']['tp_size']==4
          for rank,m in base['manifests'].items()))
    for i,row in enumerate(base['transfers']):transfer('base_rank'+str(i),row,2*Q)
    check('four_recipes_once',cases['status']=='complete' and [r['phase'] for r in cases['results']]==['SSD_only','C0','C1','C3'])
    timings=[]
    for phase,save_bytes in [('SSD_only',0),('C0',8*Q),('C1',8*Q),('C3',7*Q)]:
        row=load(a.run_dir/'cases'/f'{phase}.json')
        check(phase+'_complete',row['status']=='complete')
        if save_bytes:transfer(phase+'_save',row['save'],save_bytes)
        else:check(phase+'_no_pause_copies',not row['save'])
        transfer(phase+'_restore',row['restore'],8*Q)
        targets=row['restore_plan']['targets']
        check(phase+'_four_targets',len(targets)==4 and [t['worker'] for t in targets]==[0,2,4,5] and
              [t['tp'] for t in targets]==[0,1,2,3] and all(t['n']==2*Q and t['tier']=='hbm' for t in targets))
        preserved={r['id']:r for r in row['cleanup']['preserved_base_descriptors']}
        check(phase+'_base_generation_epoch_preserved',set(preserved)==set(base_rows) and all(
              all(preserved[rid][k]==base_rows[rid][k] for k in ('identity','epoch','generation','n','storage_node','store_endpoint')) and
              preserved[rid]['state']=='VALID' and preserved[rid]['leases']==0 for rid in base_rows))
        timing=row['restore']['controller_timing']
        timings.append(dict(phase=phase,save_bytes=save_bytes,
            filled_ms=(timing['all_targets_filled_ns']-timing['load_begin_ns'])/1e6,
            published_ms=(timing['all_targets_published_ns']-timing['load_begin_ns'])/1e6,
            prepare_ms=row['restore']['prepare_s']*1000,execute_ms=row['restore']['execute_wall_s']*1000))
    check('current_objects_released_at_run_end',cases['base_released_at_end'] is True and not final['replicas'] and
          all(not w['transactions'] and all(r['state']=='RELEASED' and r['leases']==0 for r in w['replicas']) for w in final['workers']))
    for node in ('node28','node30','node27'):
        evidence=load(a.run_dir/('exact_collect_'+node)/'stdout.json')
        after=load(a.run_dir/('after_stop_'+node)/'stdout.json')
        check(node+'_all_current_groups_empty',evidence['passed'] is True and len(evidence['reports'])==3 and
              all(r['passed'] and not r['group'] and not r['current'].get('live') for r in evidence['reports']) and
              after['cgroup']['files']['pids.current']=='1')
    passed=all(c['passed'] for c in checks)
    report=dict(status='passed' if passed else 'failed',scope='actual synthetic three-node six-worker native serial functional restoration',
        checks=checks,timings=timings,source_refs=refs,physical_peak_measured=False,model_ready=False,
        performance_repetitions=1,SSD_cold_IO_certified=False,
        NCCL_IB_log_and_old_owner_preservation_review='separate required raw-log/protection review')
    (a.out/'qualification.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(dict(status=report['status'],checks=len(checks),timings=timings)))
    if not passed:raise SystemExit(1)


if __name__=='__main__':main()
