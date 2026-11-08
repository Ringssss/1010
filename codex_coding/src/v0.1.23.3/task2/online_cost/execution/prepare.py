"""Freeze new containers and native runtime assets; setup is executed only by main."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[6]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from build_launch_plan import build, RESULTS

UPSTREAM = ROOT / 'codex_coding/src/v0.1.23.3/task2/three_node_recovery/inventory/fresh_host.py'
IMAGE = 'sha256:ee3b9807d9e90cdc3e6ab9c1058f0b78dabcafa6a0fc7661d9c5af69ce1f9588'


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def derive_setup():
    source = UPSTREAM.read_text()
    replacements = [
        ('("node28","node30")', '("node27","node30")'),
        ('(28|30)', '(27|30)'),
        ('"--memory","8g"', '"--memory",str(spec["container_hard_cap_bytes"]),"--memory-swap",str(spec["container_hard_cap_bytes"])'),
        ('"--cap-add","IPC_LOCK","--ulimit"', '"--cap-add","IPC_LOCK","--cap-add","SYS_NICE","--ulimit"'),
        ('not str(rel).startswith("codex_coding/")', 'not str(rel).startswith(("codex_coding/", "main_lib/fluidckpt/"))'),
        ('"--workdir",PROJECT,IMAGE,"infinity"]', '"--workdir",PROJECT,IMAGE,"infinity"]'),
        ('if mode=="guard":\n        result=guard(spec)', 'if mode=="guard":\n        raise SystemExit("use online review metadata collector")\n        result=guard(spec)'),
    ]
    changes = []
    for old, new in replacements:
        count = source.count(old)
        if not count:
            raise ValueError('upstream setup anchor changed: ' + old)
        source = source.replace(old, new)
        changes.append({'before': old, 'after': new, 'occurrences': count})
    check = '''    for filename, expected in (spec['model_metadata_sha256'] if spec['node'] == 'node27' else {}).items():
        model_file = Path(spec['model_path']) / filename
        with model_file.open('rb') as stream:
            raw = stream.read((4 << 20) + 1)
        if len(raw) > 4 << 20 or hashlib.sha256(raw).hexdigest() != expected:
            raise SystemExit('fresh model metadata mismatch: ' + filename)
'''
    source = source.replace('    stage.mkdir(parents=True,exist_ok=False)', check + '    stage.mkdir(parents=True,exist_ok=False)')
    source = source.replace('    run=command(argv,timeout=8)', '''    if spec['node'] == 'node27':
        argv[-2:-2] = ['--mount', f"type=bind,src={spec['model_path']},dst={spec['model_path']},readonly"]
    run=command(argv,timeout=8)''')
    compile(source, '<derived_online_setup>', 'exec')
    return source.encode(), changes


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--run-id', required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--metadata-root', type=Path, required=True)
    p.add_argument('--model-metadata', type=Path, required=True)
    p.add_argument('--simple', action='store_true', help='Freeze the approved three-GPU single-sender workflow')
    a = p.parse_args()
    out = a.out.resolve()
    if out.parent != RESULTS or not out.name.startswith('execution_setup_'):
        raise ValueError('new owned execution_setup_* output required')
    if a.simple:
        from simple_host import build_simple
        plan = build_simple(a.run_id)
    else:
        plan = build(a.run_id)
    model = json.loads(a.model_metadata.read_text())
    expected = {k: model['metadata'][k]['sha256'] for k in ('config.json', 'model.safetensors.index.json')}
    plan['model_metadata_sha256'] = expected
    plan['model_metadata_source_ref'] = {'path': str(a.model_metadata.resolve()), 'sha256': digest(a.model_metadata.read_bytes())}
    wrapper, changes = derive_setup()
    out.mkdir(parents=True, exist_ok=False)
    (out / 'setup_host.source.py').write_bytes(wrapper)
    paths = set((ROOT / 'main_lib/fluidckpt').rglob('*.py'))
    for relative in ('codex_coding/src/v0.1.23.3/task2/online_cost', 'codex_coding/src/v0.1.23.3/task2/multitier_runtime/runner', 'codex_coding/src/v0.1.23.3/task2/multitier_runtime/operations', 'codex_coding/src/v0.1.23.3/task2/minimal_calibration/measure'):
        paths.update((ROOT / relative).rglob('*.py'))
        paths.update((ROOT / relative).rglob('*.json'))
    paths.add(ROOT / 'codex_coding/src/serving/sglang_fluid_launch.py')
    paths.add(ROOT / 'codex_coding/src/v0.1.23.3/task2/three_node_recovery/admission28/runtime_gate_r03.py')
    for process in plan['processes']:
        paths.add(Path(process['env']['NCCL_TOPO_FILE']))
    assets = []
    for path in sorted(paths):
        raw = path.read_bytes()
        assets.append({'relative_path': str(path.relative_to(ROOT)), 'sha256': digest(raw),
                       'base64': base64.b64encode(raw).decode()})
    specs = {}
    for node, budgets in plan['nodes'].items():
        metadata_path = a.metadata_root / node / 'stdout.json'
        metadata = json.loads(metadata_path.read_text())
        roles = [{'gpu': r['gpu'], 'bdf': r['bdf'], 'numa': r['numa'], 'host_numa': r['numa']} for r in metadata['selected_gpus']]
        if not metadata['host_memory_admission']['passed'] or not metadata['requested_image_present'] or not metadata['candidate_name_free'] or not all(metadata['ports_free'].values()):
            raise ValueError('metadata setup gates did not pass: ' + node)
        expected_roles = {(r['gpu'], r['bdf']) for r in plan['processes'] if r['kind'] == 'worker' and r['node'] == node}
        if {(r['gpu'], r['bdf']) for r in roles} != expected_roles:
            raise ValueError('fresh metadata selected roles do not match approved plan: ' + node)
        if a.simple:
            from simple_resources import numa_plan_checks
            checks = numa_plan_checks(metadata['numa_memory'], plan['planned_source_bytes_by_numa'][node])
            if not all(row['passed'] for row in checks):
                raise ValueError('fresh selected source NUMA capacity failed: ' + node + ' ' + json.dumps(checks))
        ports = [36014, 36015, 36000, 36240, 36340] if node == 'node27' else [36010 + r['gpu'] for r in roles]
        specs[node] = dict(budgets, node=node, hostname=metadata['hostname'], boot_id=metadata['boot_id'],
                           run=a.run_id, stage=f'/tmp/fluidckpt_three_node/{a.run_id}_n{node[-2:]}',
                           image_id=IMAGE, roles=roles, ports=ports, assets=assets,
                           host_wrapper_sha256=digest(wrapper), model_path=plan['model_path'],
                           model_metadata_sha256=expected, authorization='new container setup only; zero GPU starts',
                           metadata_source_ref={'path': str(metadata_path.resolve()), 'sha256': digest(metadata_path.read_bytes())},
                           gpu=roles[0]['gpu'], bdf=roles[0]['bdf'], known_live_workers=[], keeper_endpoints=[])
        (out / (node + '.setup.spec.json')).write_text(json.dumps(specs[node], indent=2) + '\n')
        package = {'spec': specs[node], 'wrapper': base64.b64encode(wrapper).decode()}
        (out / (node + '.bootstrap.payload.json')).write_text(json.dumps(package) + '\n')
    plan['setup_specs'] = {node: str(out / (node + '.setup.spec.json')) for node in specs}
    plan['source_assets_sha256'] = {a['relative_path']: a['sha256'] for a in assets}
    (out / 'launch_plan.unbound.json').write_text(json.dumps(plan, indent=2) + '\n')
    provenance = {'upstream': str(UPSTREAM), 'upstream_sha256': digest(UPSTREAM.read_bytes()),
                  'derived_sha256': digest(wrapper), 'changes': changes,
                  'model_mount': 'explicit read-only original model directory', 'GPU_launches': 0,
                  'remote_writes': 0, 'asset_count': len(assets)}
    (out / 'provenance.json').write_text(json.dumps(provenance, indent=2) + '\n')
    print(json.dumps({'status': 'setup_frozen_not_executed', 'out': str(out), 'asset_count': len(assets)}))


if __name__ == '__main__':
    main()
