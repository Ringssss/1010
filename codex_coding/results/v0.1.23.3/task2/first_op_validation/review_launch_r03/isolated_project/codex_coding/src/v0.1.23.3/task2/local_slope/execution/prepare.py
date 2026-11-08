"""Build append-only diagnostic assets or perform fresh metadata-only preflight."""
import argparse
import base64
import copy
import json
from pathlib import Path
from common import ROOT, HERE, RESULTS, PRIOR_PREPARED, MODEL, PROTOCOL, native, ops, RUNTIME, write, ref, require, fresh_output, check_reference, quiet_readback

def partial_stage_priors(prepared, stage):
    prepared, stage = Path(prepared).resolve(), Path(stage).resolve()
    require(prepared.parent == RESULTS and stage.parent == RESULTS, 'own repair paths required')
    package = native.read(prepared / 'append_assets.json')
    append = native.read(stage / 'append_node28/stdout.json')
    require(native.read(stage / 'append_node28/command.json')['returncode'] == 0, 'node28 append did not succeed')
    require(native.read(stage / 'modefile_node28/command.json')['returncode'] != 0, 'not the expected partial-stage failure')
    failure = (stage / 'modefile_node28/stderr.txt').read_text()
    require('fresh bounded task mode path required' in failure or ('FileNotFoundError' in failure and 'minimal_calibration/measure/orchestrate_integration.py' in failure), 'stage failure differs')
    expected = {row['relative_path']:row['sha256'] for row in package['assets']}
    before_spec = native.read(prepared / 'node28.prior.spec.json')
    actual = {row['relative_path']:row['sha256'] for row in before_spec['assets']}
    for row in append['changed']:
        require(actual.get(row['relative_path']) == row['before_sha256'], 'partial append before SHA differs from predecessor prior')
        require(expected.get(row['relative_path']) == row['after_sha256'], 'partial append after SHA differs from frozen package')
        actual[row['relative_path']] = row['after_sha256']
    require(all(actual.get(path) == sha for path, sha in expected.items()), 'node28 final appended assets incomplete')
    require(actual == {row['relative_path']:row['sha256'] for row in native.read(prepared / 'node28.setup.spec.json')['assets']}, 'node28 actual append differs from setup spec')
    require(not (stage / 'append_node30').exists() and not (stage / 'append_node27').exists(), 'other nodes partially changed')
    specs = []
    for node in ('node28', 'node30', 'node27'):
        suffix = '.setup.spec.json' if node == 'node28' else '.prior.spec.json'
        spec = native.read(prepared / (node + suffix))
        require(spec['container_id'] == native.read(stage / ('before_' + node) / 'stdout.json')['container']['Id'], 'repair container identity differs')
        specs.append(spec)
    return specs

def main():
    p = argparse.ArgumentParser()
    p.add_argument('mode', choices=('build', 'preflight', 'stage'))
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--prepared', type=Path)
    p.add_argument('--session', default='local_slope_runtime_r01')
    p.add_argument('--partial-stage', type=Path)
    p.add_argument('--execute', action='store_true')
    a = p.parse_args()
    out = fresh_output(a.out)
    if a.mode == 'build':
        old_path = PRIOR_PREPARED / 'launch_plan.json'
        prior = native.read(old_path)
        require(a.session.startswith('local_slope_runtime_') and '/' not in a.session, 'fresh task session required')
        check_reference(prior['frozen_model_ref'])
        plan = copy.deepcopy(prior)
        mode_path = RESULTS / (a.session + '.inventory_mode.json')
        plan.update(session=a.session, setup_directory=str(out), prior_setup_directory=str(PRIOR_PREPARED),
                    prior_plan_ref=ref(old_path), inventory_mode_file=str(mode_path), protocol=PROTOCOL,
                    frozen_model_ref=ref(MODEL), model_consumed=False, retry_reason=None)
        assets = [dict(relative_path=str(path.relative_to(ROOT)), sha256=native.sha(path),
                       base64=base64.b64encode(path.read_bytes()).decode()) for path in sorted(HERE.glob('*.py'))]
        for row in plan['processes']:
            row['out'] = str(ops.RESULTS / f'operations_process_{a.session}_{row["key"]}')
            if row['kind'] == 'worker':
                row['argv'][row['argv'].index('--session') + 1] = a.session
                row['argv'][row['argv'].index('--inventory-mode-file') + 1] = str(mode_path)
                row['env']['NCCL_TOPO_DUMP_FILE'] = str(ops.RESULTS / ('operations_dump_' + a.session) / (row['key'] + '.runtime.dump.xml'))
                if row['worker'] == 4:
                    row['argv'][1] = str(HERE / 'worker.py')
        if a.partial_stage:
            require(a.prepared is not None, 'partial stage needs predecessor prepared')
            specs = partial_stage_priors(a.prepared, a.partial_stage)
            plan['partial_stage_repair'] = dict(predecessor_plan_ref=ref(a.prepared / 'launch_plan.json'),
                actual_node28_append_ref=ref(a.partial_stage / 'append_node28/stdout.json'),
                modefile_failure_ref=ref(a.partial_stage / 'modefile_node28/command.json'), GPU_launches=0,
                node_state={'node28':f'predecessor {len(native.read(a.prepared / "append_assets.json")["assets"])} task source assets appended; mode file absent', 'node30':'actual predecessor prior assets unchanged', 'node27':'actual predecessor prior assets unchanged'})
        else:
            specs = ops.specs(PRIOR_PREPARED)
        for spec in specs:
            previous = copy.deepcopy(spec)
            previous['assets'] = [{k:v for k,v in row.items() if k != 'base64'} for row in previous['assets']]
            write(out / (spec['node'] + '.prior.spec.json'), previous)
            merged = {row['relative_path']:row for row in previous['assets']}
            for row in assets:
                if row['relative_path'] in merged:
                    require(a.partial_stage is not None and row['relative_path'].startswith(str(HERE.relative_to(ROOT)) + '/'), 'would overwrite a frozen prior asset')
                merged[row['relative_path']] = {k:v for k,v in row.items() if k != 'base64'}
            spec['assets'] = list(merged.values())
            write(out / (spec['node'] + '.setup.spec.json'), spec)
        write(out / 'append_assets.json', dict(assets=assets, revision='local_slope_' + out.name, inventory_mode_file=str(mode_path)))
        write(out / 'launch_plan.json', plan)
        write(out / 'manifest.json', dict(plan_ref=ref(out / 'launch_plan.json'), prior_plan_ref=ref(old_path),
              frozen_model_ref=ref(MODEL), model_consumed=False, GPU_launches=0, remote_writes=0, append_only=True))
        print(str(out / 'launch_plan.json'))
        return
    prepared = (a.prepared or PRIOR_PREPARED).resolve()
    plan = native.read(prepared / 'launch_plan.json')
    check_reference(plan['frozen_model_ref'])
    specs = ops.specs(prepared) if a.mode == 'preflight' else [native.read(prepared / (node + '.prior.spec.json')) for node in ('node28', 'node30', 'node27')]
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
        proc, raw = ops.old.record(ops.command(node, ['python3', str(helper)]), out / ('append_' + node), 30,
                                 json.dumps(dict(spec=spec, revision=package['revision'], assets=package['assets'])).encode())
        require(proc['returncode'] == 0, 'append source failed ' + node)
        write(out / ('append_' + node) / 'stdout.json', json.loads(raw))
        mode_helper = HERE / 'mode_file.py'
        proc, raw = ops.old.record(ops.command(node, ['docker', 'exec', spec['container_id'], 'python3', str(mode_helper), '--path', package['inventory_mode_file']]), out / ('modefile_' + node), 15)
        require(proc['returncode'] == 0, 'fresh cached mode file creation failed ' + node)
        quiet_readback(native.read(prepared / (node + '.setup.spec.json')), out / ('after_' + node))
    write(out / 'manifest.json', dict(GPU_launches=0, append_only=True, prepared_ref=ref(prepared / 'launch_plan.json')))

if __name__ == '__main__':
    main()
