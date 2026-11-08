"""Freeze a new launch plan and append assets to exact quiet prior stages."""
import argparse
import base64
import copy
import json
from pathlib import Path
from common import ROOT, HERE, RESULTS, CALIBRATION, RUNTIME, native, ops, write, ref, require, fresh_output, check_reference, quiet_readback

def main():
    p = argparse.ArgumentParser()
    p.add_argument('mode', choices=('build', 'preflight', 'stage'))
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--session', default='bounded_cost_runtime_r01')
    p.add_argument('--model', type=Path)
    p.add_argument('--prepared', type=Path)
    p.add_argument('--execute', action='store_true')
    a = p.parse_args()
    out = fresh_output(a.out)
    if a.mode == 'build':
        require(a.model is not None, 'frozen old-training model required')
        model_path = a.model.resolve()
        require(model_path.is_file() and model_path.is_relative_to(RESULTS), 'bounded frozen model missing/outside result scope')
        old_path = CALIBRATION / 'execution_prepared_r02/launch_plan.json'
        prior = native.read(old_path)
        require(a.session.startswith('bounded_cost_runtime_') and '/' not in a.session and a.session != prior['session'], 'fresh bounded session required')
        plan = copy.deepcopy(prior)
        mode_path = RESULTS / (a.session + '.inventory_mode.json')
        plan.update(session=a.session, prior_plan_ref=ref(old_path), setup_directory=str(out), prior_setup_directory=prior['setup_directory'], inventory_mode_file=str(mode_path), paired_query_holdout=False, frozen_model_ref=ref(model_path), protocol=dict(mode='cached', training_requests=0, query_AB_requests=0, validation_requests=2, novel_size_bytes=8 << 20, mixed_bytes=24 << 20, necessary_pairs=[[2, 4], [4, 5]], migration_validation=True))
        assets = []
        for path in sorted(HERE.glob('*.py')):
            raw = path.read_bytes()
            assets.append(dict(relative_path=str(path.relative_to(ROOT)), sha256=native.sha(path), base64=base64.b64encode(raw).decode()))
        for row in plan['processes']:
            row['out'] = str(ops.RESULTS / f'operations_process_{a.session}_{row["key"]}')
            if row['kind'] == 'worker':
                idx = row['argv'].index('--session')
                row['argv'][idx + 1] = a.session
                idx = row['argv'].index('--inventory-mode-file')
                row['argv'][idx + 1] = str(mode_path)
                row['env']['NCCL_TOPO_DUMP_FILE'] = str(ops.RESULTS / ('operations_dump_' + a.session) / (row['key'] + '.runtime.dump.xml'))
        for spec in ops.specs(Path(prior['setup_directory'])):
            previous = copy.deepcopy(spec)
            previous['assets'] = [{k: v for k, v in row.items() if k != 'base64'} for row in previous['assets']]
            write(out / (spec['node'] + '.prior.spec.json'), previous)
            merged = {row['relative_path']: row for row in previous['assets']}
            for row in assets:
                require(row['relative_path'] not in merged, 'new execution asset would overwrite prior source')
                merged[row['relative_path']] = {k: v for k, v in row.items() if k != 'base64'}
            spec['assets'] = list(merged.values())
            write(out / (spec['node'] + '.setup.spec.json'), spec)
        write(out / 'append_assets.json', dict(assets=assets, revision='bounded_cost_' + out.name, inventory_mode_file=str(mode_path)))
        write(out / 'launch_plan.json', plan)
        write(out / 'manifest.json', dict(GPU_launches=0, remote_writes=0, append_only=True, plan_ref=ref(out / 'launch_plan.json'), frozen_model_ref=ref(model_path), prior_plan_ref=ref(old_path)))
        print(json.dumps(dict(plan=str(out / 'launch_plan.json'), GPU_launches=0, remote_writes=0)))
        return
    require(a.prepared is not None, 'prepared directory required')
    prepared = a.prepared.resolve()
    plan = native.read(prepared / 'launch_plan.json')
    if 'frozen_model_ref' in plan:
        check_reference(plan['frozen_model_ref'])
    suffix = '.setup.spec.json' if a.mode == 'preflight' else '.prior.spec.json'
    specs = [native.read(prepared / (node + suffix)) for node in ('node28', 'node30', 'node27')]
    # Validate all nodes before the first append; no silent container/boot remapping.
    for spec in specs:
        quiet_readback(spec, out / ('before_' + spec['node']))
    write(out / 'preflight.json', dict(passed=True, GPU_launches=0, remote_writes=0, plan_ref=ref(prepared / 'launch_plan.json')))
    if a.mode == 'preflight':
        print(json.dumps(dict(passed=True, GPU_launches=0, remote_writes=0)))
        return
    require(a.execute, 'main-agent stage execution requires --execute')
    package = native.read(prepared / 'append_assets.json')
    for spec in specs:
        node = spec['node']
        helper = Path(spec['stage']) / 'project' / (RUNTIME / 'operations/stage_assets.py').relative_to(ROOT)
        payload = dict(spec=spec, revision=package['revision'], assets=package['assets'])
        proc, raw = ops.old.record(ops.command(node, ['python3', str(helper)]), out / ('append_' + node), 30, json.dumps(payload).encode())
        require(proc['returncode'] == 0, 'append source failed ' + node)
        write(out / ('append_' + node) / 'stdout.json', json.loads(raw))
        proc, raw = ops.old.record(ops.command(node, ['docker', 'exec', spec['container_id'], 'python3', str(HERE / 'mode_file.py'), '--path', package['inventory_mode_file']]), out / ('modefile_' + node), 15)
        require(proc['returncode'] == 0, 'fresh cached mode file creation failed ' + node)
        write(out / ('modefile_' + node) / 'stdout.json', json.loads(raw))
        quiet_readback(native.read(prepared / (node + '.setup.spec.json')), out / ('after_' + node))
    write(out / 'manifest.json', dict(GPU_launches=0, append_only=True, prepared_ref=ref(prepared / 'launch_plan.json')))

if __name__ == '__main__':
    main()
