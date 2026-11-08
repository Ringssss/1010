"""Replay real fixture schemas through native preparation and frozen prediction."""
import argparse
import copy
from pathlib import Path
import sys
import time
from common import CALIBRATION, native, write, ref, require, fresh_output
import runner

class StopAfterFrozenPredictions(RuntimeError):
    pass

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--actual',type=Path,required=True)
    p.add_argument('--prepared',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    a = p.parse_args()
    out = fresh_output(a.out)
    launch_path = a.prepared.resolve()/'launch_plan.json'
    launch = native.read(launch_path)
    workers = native.read(a.actual/'mesh/mesh.json')['workers']
    setup = native.read(a.actual/'runtime_setup.json')
    setup.update(session=launch['session'],valid_until=time.time()+120,evidence_refs={'plan':ref(launch_path)},CPU_REPLAY_ONLY=True)
    for w in workers:
        w.update(session=launch['session'],epoch='cpu_replay/'+str(w['worker']),replicas=[],used_bytes=dict(dram=0,hbm=0,ssd=0),transactions=[])
    setup_path = out/'runtime_setup.json'
    write(setup_path,setup)
    mesh = native.read(a.actual/'mesh/mesh.json')
    mesh.update(workers=workers,incarnations=runner.new_state(workers)['incarnations'],CPU_REPLAY_ONLY=True)
    (out/'mesh').mkdir()
    write(out/'mesh/mesh.json',mesh)
    previous = CALIBRATION/'execution_actual_r02/measure'
    synth = native.read(previous/'journal.json')['synth']
    bootstraps = {index:native.read(previous/f'bootstrap{index}.json') for index in (0,2)}
    pending_synth = {}
    replicas = {}
    verification_keys = []

    def inventory_rows():
        rows = copy.deepcopy(workers)
        for w in rows:
            w['replicas']=[copy.deepcopy(r) for r in replicas.values() if r['worker']==w['worker']]
            w['used_bytes']={tier:sum(r['n'] for r in w['replicas'] if r['tier']==tier and r['state']=='VALID') for tier in ('dram','hbm','ssd')}
        return rows

    def request(endpoint,operation,body):
        worker = next(w for w in workers if w['endpoint']==endpoint) if 'endpoint' in workers[0] else next(w for w in setup['workers'] if w['endpoint']==endpoint)
        index = worker['worker']
        if operation=='synth':
            pending_synth[index]=body['tp']
            return copy.deepcopy(synth[body['tp']]['diagnostics'])
        if operation=='adopt':
            row=copy.deepcopy(synth[pending_synth[index]]['row'])
            row.update(id=body['id'],epoch=workers[index]['epoch'])
            replicas[row['id']]=row
            return copy.deepcopy(row)
        if operation=='retire':
            replicas[body['id']]['state']='RELEASED'
            return {'released':True}
        raise AssertionError('unexpected native request '+operation)

    class ReplayCluster(runner.Cluster):
        def call_all(self,operation,bodies=None,telemetry=None):
            if operation.startswith('inventory'):
                return inventory_rows()
            if operation=='verify_run':
                plan=self.active[bodies[0]['tx']]
                op=plan['ops'][0]
                index=0 if plan['targets'][0]['tier']=='hbm' else 2
                old=next(iter(bootstraps[index]['verification']['operations'].values()))
                source={key:old[key] for key in ('n','source_sha256')}
                target={key:old[key] for key in ('n','target_sha256')}
                source['op_id']=target['op_id']=op['op_id']
                rows=[dict(worker=w['worker'],rows=[]) for w in workers]
                rows[op['src_w']]['rows'].append(source)
                rows[op['dst_w']]['rows'].append(target)
                return rows
            raise AssertionError('unexpected native call_all '+operation)
        def transfer(self,plan):
            if plan['ops'][0]['n']==8 << 20:
                require((out/'measure/prediction_freeze.json').exists(),'both predictions must exist before simulated first request')
                raise StopAfterFrozenPredictions('CPU replay intentionally stops before holdout execution')
            tx='cpu_bootstrap'+str(len(verification_keys))
            self.active[tx]=plan
            verification=runner.Cluster.verify(self,tx)
            verification_keys.append(list(verification['operations']))
            target=copy.deepcopy(plan['targets'][0])
            w=workers[target['worker']]
            target.update(epoch=w['epoch'],generation=1,node=w['node'],gpu=w['gpu'],state='VALID',leases=0)
            replicas[target['id']]=target
            index=0 if target['tier']=='hbm' else 2
            flow=copy.deepcopy(bootstraps[index])
            flow.update(plan=plan,verification=verification,CPU_REPLAY_ONLY=True)
            return flow

    def ssd_stats(unused):
        status=native.read(CALIBRATION/'execution_actual_r02/measure/final_inventory.json')['ssd']
        status['n2']['files']=sum(r['tier']=='ssd' and r['state']=='VALID' for r in replicas.values())
        return status

    runner.Cluster=ReplayCluster
    native.request=request
    native.ssd_stats=ssd_stats
    sys.argv=['runner.py','--setup',str(setup_path),'--plan',str(launch_path),'--model',launch['frozen_model_ref']['path'],'--output',str(out/'measure'),'--stage','measure']
    try:
        runner.main()
    except StopAfterFrozenPredictions:
        pass
    else:
        raise AssertionError('replay must stop before simulating a measured result')
    require(verification_keys==[[0],[0]],'native Cluster.verify must return integer operation keys for both real fixture schemas')
    frozen=native.read(out/'measure/prediction_freeze.json')
    require(len(frozen['predictions'])==2,'two preregistered predictions missing')
    for reference in frozen['predictions'].values():
        runner.check_reference(reference)
    final=native.read(out/'measure/final_inventory.json')
    require(not final['replicas'] and all(not any(w['used_bytes'].values()) for w in final['workers']) and all(s['files']==0 for s in final['ssd'].values()),'native exact release did not clear replay payload')
    bad=copy.deepcopy(bootstraps[0])
    next(iter(bad['verification']['operations'].values()))['target_sha256']='wrong'
    try:
        runner.bootstrap_content_matches(bad,bootstraps[0])
    except ValueError:
        pass
    else:
        raise AssertionError('wrong checksum accepted')
    checks=['native synth_adopt replays three real source rows','two actual Cluster.verify merges return int op IDs','real old JSON str keys compare by plan op ID','bootstrap evidence saved before content assertion','whole preparation source-schema and physical migration passes','bind_session passes with exact fresh source lineage','both preregistration predictions freeze','in-window plan/prediction equality passes before intentional stop','native exact_release clears all replay objects/SSD','wrong SHA rejected']
    write(out/'qualification.json',dict(passed=True,checks=checks,CPU_REPLAY_ONLY=True,holdout_measurements_simulated=0,source_actual_ref=ref(a.actual/'mesh/mesh.json'),old_bootstrap_refs=[ref(previous/f'bootstrap{i}.json') for i in (0,2)],launch_ref=ref(launch_path),runner_ref=ref(Path(runner.__file__)),GPU_launches=0,remote_writes=0))
    print(str(out/'qualification.json'))

if __name__=='__main__':
    main()
