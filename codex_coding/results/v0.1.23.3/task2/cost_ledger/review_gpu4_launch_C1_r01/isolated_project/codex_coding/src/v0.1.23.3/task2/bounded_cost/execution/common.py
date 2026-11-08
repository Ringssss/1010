"""Reuse corrected calibration infrastructure without modifying frozen sources."""
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[6]
HERE = Path(__file__).resolve().parent
RESULTS = ROOT / 'codex_coding/results/v0.1.23.3/task2/bounded_cost'
CALIBRATION = ROOT / 'codex_coding/results/v0.1.23.3/task2/cost_calibration'
OLD = ROOT / 'codex_coding/src/v0.1.23.3/task2/cost_calibration/execution'
sys.path.insert(0, str(ROOT / 'main_lib'))

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

previous = load('bounded_previous_common', OLD / 'common.py')
native, parent, ops, RUNTIME = previous.native, previous.parent, previous.ops, previous.RUNTIME
write, ref, require = native.write, native.ref, native.require
budget_snapshot = previous.budget_snapshot

def fresh_output(path):
    path = Path(path).resolve()
    require(path.parent == RESULTS and path.name.startswith('execution_'), 'fresh bounded execution output required')
    path.mkdir(parents=True, exist_ok=False)
    return path

def check_reference(reference):
    require(ref(reference['path']) == reference, 'frozen reference changed: ' + reference['path'])

def quiet_readback(spec, out):
    body = ops.readback(spec, out)
    require(body['boot_id'].strip() == spec['boot_id'], 'boot changed: ' + spec['node'])
    ci = body['container']
    require(ci.get('Id') == spec['container_id'] and ci['State']['StartedAt'] == spec['container_started_at'] and ci['State']['Running'], 'exact running container changed: ' + spec['node'])
    require(body['cgroup']['files']['pids.current'] == '1', 'task container is not quiet: ' + spec['node'])
    require(all(body['ports_free'].values()), 'selected ports occupied: ' + spec['node'])
    require(all(body['source_assets'].get(row['relative_path']) == row['sha256'] for row in spec['assets']), 'frozen deployed assets changed: ' + spec['node'])
    return body
