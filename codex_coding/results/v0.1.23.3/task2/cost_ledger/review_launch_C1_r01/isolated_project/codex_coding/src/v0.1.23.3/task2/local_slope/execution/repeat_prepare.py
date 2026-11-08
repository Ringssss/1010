"""Append only four new repeat-diagnostic assets to the verified completed stage."""
import argparse
import base64
import copy
import json
from pathlib import Path
from common import ROOT, HERE, RESULTS, native, ops, RUNTIME, write, ref, require, fresh_output, check_reference, quiet_readback
from repeat_runner import PROTOCOL

PREVIOUS = RESULTS / 'execution_prepared_r03'

def main():
    p = argparse.ArgumentParser()
    p.add_argument('mode', choices=('build','stage','preflight'))
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--prepared', type=Path)
    p.add_argument('--session', default='local_slope_runtime_r02')
    p.add_argument('--execute', action='store_true')
    a = p.parse_args()
    out = fresh_output(a.out)
    if a.mode == 'build':
        prior = native.read(PREVIOUS / 'launch_plan.json')
        require(a.session.startswith('local_slope_runtime_') and '/' not in a.session and a.session != prior['session'], 'fresh second diagnostic session required')
        check_reference(prior['frozen_model_ref'])
        require(native.read(RESULTS / 'execution_actual_r01/summary.json')['status'] == 'completed_pending_independent_review', 'first session completion missing')
        plan = copy.deepcopy(prior)
        mode_path = RESULTS / (a.session + '.inventory_mode.json')
        plan.update(session=a.session, setup_directory=str(out), prior_setup_directory=str(PREVIOUS),
            prior_plan_ref=ref(PREVIOUS / 'launch_plan.json'), inventory_mode_file=str(mode_path), protocol=PROTOCOL,
            first_actual_ref=ref(RESULTS / 'execution_actual_r01/summary.json'), model_consumed=False)
        plan.pop('partial_stage_repair', None)
        names = ('repeat_worker.py','repeat_runner.py','repeat_prepare.py','repeat_session.py')
        assets = [dict(relative_path=str((HERE/name).relative_to(ROOT)), sha256=native.sha(HERE/name),
            base64=base64.b64encode((HERE/name).read_bytes()).decode()) for name in names]
        for row in plan['processes']:
            row['out'] = str(ops.RESULTS / f'operations_process_{a.session}_{row["key"]}')
            if row['kind'] == 'worker':
                row['argv'][row['argv'].index('--session')+1] = a.session
                row['argv'][row['argv'].index('--inventory-mode-file')+1] = str(mode_path)
                row['env']['NCCL_TOPO_DUMP_FILE'] = str(ops.RESULTS / ('operations_dump_'+a.session) / (row['key']+'.runtime.dump.xml'))
                if row['worker'] == 4:
                    row['argv'][1] = str(HERE / 'repeat_worker.py')
        for spec in ops.specs(PREVIOUS):
            previous = copy.deepcopy(spec)
            previous['assets'] = [{k:v for k,v in row.items() if k != 'base64'} for row in previous['assets']]
            write(out / (spec['node']+'.prior.spec.json'), previous)
            existing = {row['relative_path'] for row in previous['assets']}
            require(all(row['relative_path'] not in existing for row in assets), 'would overwrite a first-stage frozen source')
            spec['assets'] = previous['assets'] + [{k:v for k,v in row.items() if k != 'base64'} for row in assets]
            write(out / (spec['node']+'.setup.spec.json'), spec)
        write(out / 'append_assets.json', dict(assets=assets, revision='local_slope_'+out.name, inventory_mode_file=str(mode_path)))
        write(out / 'launch_plan.json', plan)
        write(out / 'manifest.json', dict(plan_ref=ref(out/'launch_plan.json'), GPU_launches=0, remote_writes=0,
            frozen_model_ref=plan['frozen_model_ref'], append_only=True, new_source_files=4, further_GPU_requests_after_this_session=0))
        print(str(out / 'launch_plan.json'))
        return
    require(a.prepared is not None, 'prepared directory required')
    prepared = a.prepared.resolve()
    plan = native.read(prepared / 'launch_plan.json')
    require(plan['protocol'] == PROTOCOL, 'last two-request protocol differs')
    check_reference(plan['frozen_model_ref'])
    suffix = '.prior.spec.json' if a.mode == 'stage' else '.setup.spec.json'
    specs = [native.read(prepared / (node+suffix)) for node in ('node28','node30','node27')]
    for spec in specs:
        quiet_readback(spec, out / ('before_'+spec['node']))
    write(out / 'preflight.json', dict(passed=True, GPU_launches=0, remote_writes=0, plan_ref=ref(prepared/'launch_plan.json')))
    if a.mode == 'preflight':
        return
    require(a.execute, 'main-agent stage requires --execute')
    package = native.read(prepared / 'append_assets.json')
    for spec in specs:
        node = spec['node']
        helper = Path(spec['stage'])/'project'/(RUNTIME/'operations/stage_assets.py').relative_to(ROOT)
        proc, raw = ops.old.record(ops.command(node,['python3',str(helper)]), out/('append_'+node),30,
            json.dumps(dict(spec=spec,revision=package['revision'],assets=package['assets'])).encode())
        require(proc['returncode'] == 0,'four new assets append failed '+node)
        write(out/('append_'+node)/'stdout.json',json.loads(raw))
        proc, raw = ops.old.record(ops.command(node,['docker','exec',spec['container_id'],'python3',str(HERE/'mode_file.py'),
            '--path',package['inventory_mode_file']]),out/('modefile_'+node),15)
        require(proc['returncode'] == 0,'pure-stdlib fixed mode CLI failed '+node)
        quiet_readback(native.read(prepared/(node+'.setup.spec.json')),out/('after_'+node))
    write(out/'manifest.json',dict(GPU_launches=0,append_only=True,prepared_ref=ref(prepared/'launch_plan.json')))

if __name__ == '__main__':
    main()
