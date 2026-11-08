"""Check frozen launch assets and explicit CPU replay limits without remote access."""
import argparse
import ast
import base64
import hashlib
import json
import contextlib
import io
import sys
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
EXEC = ROOT / 'codex_coding/src/v0.1.23.3/task2/local_slope/execution'


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--prepared', required=True, type=Path)
    parser.add_argument('--cpu', required=True, type=Path)
    parser.add_argument('--stage-cpu', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    checks, refs = [], []

    def check(name, predicate):
        checks.append({'name': name, 'passed': bool(predicate)})
        assert predicate, name

    def take(path):
        refs.append({'path': str(path.resolve()), 'sha256': sha(path)})
        return read(path)

    plan = take(args.prepared / 'launch_plan.json')
    old = take(Path(plan['prior_plan_ref']['path']))
    package = take(args.prepared / 'append_assets.json')
    model_path = Path(plan['frozen_model_ref']['path'])
    model = take(model_path)
    check('model file frozen SHA', sha(model_path) == plan['frozen_model_ref']['sha256'])
    check('frozen model file identity', sha(model_path) == 'e98bbed04d5ce502439acea4fd8fabd554dd67e69ca2b94ebba784ab8535734f')
    check('no model consumption', plan['model_consumed'] is False)
    for key in ('workers', 'ssd', 'lifetime_s', 'startup_s'):
        check('original resource/config ' + key, plan[key] == old[key])
    check('six workers nine processes', len(plan['workers']) == 6 and len(plan['processes']) == 9)
    check('only nodes28/30/27', {p['node'] for p in plan['processes']} == {'node28', 'node30', 'node27'})
    protocol = plan['protocol']
    cases = protocol['cases']
    check('eight fixed mode/size cases', [(c['mode'], c['nbytes']) for c in cases]
          == [(mode, size << 20) for mode in ('native', 'diagnostic') for size in (4, 16, 16, 4)])
    check('original local source/target', protocol['source_worker'] == protocol['target_worker'] == 4
          and protocol['source_tier'] == protocol['target_tier'] == 'hbm'
          and protocol['source_bytes'] == 16 << 20)
    check('original executor/chunk/target', protocol['executor'] == 'serial'
          and protocol['chunk_bytes'] == 32 << 20 and protocol['target_allocation'] == 'fresh exact size')
    check('no fits/train/model updates', all(protocol[k] == 0 for k in ('fits', 'training_requests', 'model_updates')))
    check('mode block drift explicit', 'drift' in protocol['ordering_limit'])
    check('extra event preparation explicit', 'synchronized' in protocol['diagnostic_prepare'])
    old_procs = {p['key']: p for p in old['processes']}
    for process in plan['processes']:
        prior = old_procs[process['key']]
        for key in ('node', 'kind', 'container_id'):
            check(process['key'] + ': original ' + key, process[key] == prior[key])
        if process['kind'] == 'worker':
            index = process['argv'].index('--session')
            check(process['key'] + ': fresh task session', process['argv'][index+1] == plan['session'])
            worker = process['worker']
            check(process['key'] + ': overlay only worker4',
                  process['argv'][1] == str(EXEC / 'worker.py') if worker == 4 else process['argv'][1] == prior['argv'][1])
            check(process['key'] + ': original topo input', process['env'].get('NCCL_TOPO_FILE') == prior['env'].get('NCCL_TOPO_FILE'))
            check(process['key'] + ': task own topology dump', plan['session'] in process['env']['NCCL_TOPO_DUMP_FILE']
                  and process['env']['NCCL_TOPO_DUMP_FILE'] != process['env'].get('NCCL_TOPO_FILE'))
    for asset in package['assets']:
        path = ROOT / asset['relative_path']
        check('appended source exact ' + path.name, sha(path) == asset['sha256']
              and hashlib.sha256(base64.b64decode(asset['base64'])).hexdigest() == asset['sha256'])
        refs.append({'path': str(path), 'sha256': sha(path)})
        if path.suffix == '.py':
            ast.parse(path.read_text())
    partial = plan.get('partial_stage_repair')
    predecessor = None
    if partial:
        predecessor = Path(partial['predecessor_plan_ref']['path']).parent
        append = take(Path(partial['actual_node28_append_ref']['path']))
        failure_path = Path(partial['modefile_failure_ref']['path'])
        failure = take(failure_path)
        old_package = take(predecessor / 'append_assets.json')
        old_prior = take(predecessor / 'node28.prior.spec.json')
        actual_assets = {a['relative_path']:a['sha256'] for a in old_prior['assets']}
        package_assets = {a['relative_path']:a['sha256'] for a in old_package['assets']}
        for asset in append['changed']:
            check('partial append before SHA ' + asset['relative_path'], actual_assets.get(asset['relative_path']) == asset['before_sha256'])
            check('partial append after SHA ' + asset['relative_path'], package_assets.get(asset['relative_path']) == asset['after_sha256'])
            actual_assets[asset['relative_path']] = asset['after_sha256']
        check('actual partial final SHA matches predecessor', all(actual_assets.get(k) == v for k, v in package_assets.items()))
        check('partial append no GPU', append['GPU_launches'] == 0)
        check('observed mode path rejection before file creation',
              failure['returncode'] != 0 and ('fresh bounded task mode path required' in (failure_path.parent / 'stderr.txt').read_text()
              or 'minimal_calibration/measure/orchestrate_integration.py' in (failure_path.parent / 'stderr.txt').read_text()))
        check('other two nodes not appended in failed stage', not (failure_path.parents[1] / 'append_node30').exists()
              and not (failure_path.parents[1] / 'append_node27').exists())
        check('only prepare/helper changed',
              {a['relative_path'] for a in package['assets'] if a['relative_path'] in
               {b['relative_path'] for b in old_package['assets']} and a['sha256'] !=
               next(b['sha256'] for b in old_package['assets'] if b['relative_path'] == a['relative_path'])}
              <= {str((EXEC / 'prepare.py').relative_to(ROOT)), str((EXEC / 'mode_file.py').relative_to(ROOT))})
    for node in ('node28', 'node30', 'node27'):
        prior = take(args.prepared / (node + '.prior.spec.json'))
        current = take(args.prepared / (node + '.setup.spec.json'))
        old_assets = {a['relative_path']:a for a in prior['assets']}
        new_assets = {a['relative_path']:a for a in current['assets']}
        mutable = {str((EXEC / name).relative_to(ROOT)) for name in ('prepare.py', 'mode_file.py')} if partial and node == 'node28' else set()
        check(node + ': original assets intact', all(new_assets[k] == v for k, v in old_assets.items() if k not in mutable))
        check(node + ': only appended task assets', set(new_assets) - set(old_assets)
              == {a['relative_path'] for a in package['assets']} - set(old_assets))
        if partial:
            expected = take(predecessor / (node + ('.setup.spec.json' if node == 'node28' else '.prior.spec.json')))
            check(node + ': partial stage predecessor reconstructed', prior == expected)
    worker_text = (EXEC / 'worker.py').read_text()
    check('native controls invoke original immutable method', 'original.NATIVE_WORKER._execute(self, body)' in worker_text)
    check('instrumentation and drift labelled', 'phase_observer_overhead_included=True' in worker_text
          and 'zero is not proof of zero observer overhead' in worker_text and 'temporal drift confounded' in worker_text)
    check('event window not pure DMA', 'not a pure kernel duration' in worker_text)
    check('post-service diagnosis separately recorded', 'post_service_diagnosis_wall_ns' in worker_text)
    cpu = take(args.cpu / 'qualification.json')
    check('CPU replay passed explicitly GPU zero', cpu['passed'] and cpu['CPU_REPLAY_ONLY']
          and cpu['GPU_launches'] == cpu['actual_GPU_measurements'] == cpu['remote_writes'] == 0)
    check('all eight CPU workflow cases', cpu['simulated_requests'] == 8)
    check('CPU hash/timer oracles disclosed', 'metadata oracles' in cpu['fixture_digest_policy']
          and 'not GPU timing evidence' in cpu['fixture_digest_policy'])
    measurements = take(args.cpu / 'measure/measurements.json')
    check('CPU replay exact actual case objects', [r['case'] for r in measurements] == cases)
    check('CPU copied bytes independently verified', cpu['copied_bytes'] == (16 << 20) + sum(c['nbytes'] for c in cases))
    if args.stage_cpu:
        stage_cpu = take(args.stage_cpu / 'qualification.json')
        check('stage CPU path gate passed with zero GPU/remote', stage_cpu['passed'] and stage_cpu['CPU_ONLY']
          and stage_cpu['GPU_launches'] == stage_cpu['remote_writes'] == 0 and stage_cpu['node28_staged_assets'] == len(old_package['assets']))
        helper = EXEC / 'mode_file.py'
        refs.append({'path': str(helper), 'sha256': sha(helper)})
        isolated = args.output.resolve() / 'isolated'
        isolated.mkdir()
        copied = isolated / 'mode_file.py'
        copied.write_bytes(helper.read_bytes())
        scope = isolated / 'local_slope'
        target = scope / 'local_slope_runtime_review.inventory_mode.json'
        def run_helper(path, label):
            cmd = [sys.executable, '-I', str(copied), '--path', str(path)]
            proc = subprocess.run(cmd, cwd=isolated, capture_output=True, text=True, timeout=10)
            (args.output / (label + '.command.json')).write_text(json.dumps({'argv': cmd, 'cwd': str(isolated), 'returncode': proc.returncode,
                'stdout': proc.stdout, 'stderr': proc.stderr}, indent=2) + '\n')
            return proc
        proc = run_helper(target, 'isolated_create')
        check('isolated exact helper CLI no checkout dependencies', proc.returncode == 0 and read(target) == {'mode': 'cached'})
        before = sha(target)
        proc = run_helper(target, 'isolated_duplicate')
        check('helper duplicate refuses overwrite', proc.returncode != 0 and 'FileExistsError' in proc.stderr and sha(target) == before)
        for label, bad in [('prefix', scope / 'bounded_cost_runtime_review.inventory_mode.json'),
                           ('parent', isolated / target.name), ('suffix', scope / 'local_slope_runtime_review.bad')]:
            proc = run_helper(bad, 'isolated_wrong_' + label)
            check('helper rejects incorrect ' + label, proc.returncode != 0 and not bad.exists())
        check('worker excludes coordinator common imports', not any(isinstance(node, ast.ImportFrom) and node.module == 'common'
              for node in ast.walk(ast.parse(worker_text))))
        spec_assets = {a['relative_path'] for a in read(args.prepared / 'node27.setup.spec.json')['assets']}
        for rel in ('codex_coding/src/v0.1.23.3/task2/cost_calibration/inventory/worker.py',
                    'codex_coding/src/v0.1.23.3/task2/cost_calibration/inventory/identity_diagnostics.py',
                    'codex_coding/src/v0.1.23.3/task2/multitier_runtime/runner/runtime_worker.py',
                    'main_lib/fluidckpt/cluster/worker.py', 'main_lib/fluidckpt/serving/keeper.py'):
            check('worker inherited dependency staged ' + rel, rel in spec_assets)
    result = {'status': 'local_slope_cpu_launch_review_passed', 'GPU_launches': 0,
              'remote_calls': 0, 'checks': checks, 'source_refs': refs,
              'scope': 'Append-only source/launch qualification and real native CPU workflow with doubles; GPU behavior unmeasured.',
              'limitations': ['Native block and diagnostic block retain temporal drift confounding.',
                              'Diagnostic preparation has extra event initialization/synchronization.',
                              'Expanded local branch retains copy bytes, but diagnostic clock/dispatch work differs.',
                              'CPU tensors/events/historical SHA oracles do not validate GPU timing.']}
    (args.output / 'qualification.json').write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'status': result['status'], 'checks': len(checks), 'refs': len(refs)}))


if __name__ == '__main__':
    main()
