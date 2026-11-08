"""Reuse the corrected finite-session infrastructure without editing frozen assets."""
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[6]
HERE = Path(__file__).resolve().parent
RESULTS = ROOT / 'codex_coding/results/v0.1.23.3/task2/local_slope'
BOUNDED = ROOT / 'codex_coding/results/v0.1.23.3/task2/bounded_cost'
CALIBRATION = ROOT / 'codex_coding/results/v0.1.23.3/task2/cost_calibration'
sys.path.insert(0, str(ROOT / 'main_lib'))

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

prior = load('local_slope_bounded_common', ROOT / 'codex_coding/src/v0.1.23.3/task2/bounded_cost/execution/common.py')
native, parent, ops, RUNTIME = prior.native, prior.parent, prior.ops, prior.RUNTIME
write, ref, require = native.write, native.ref, native.require
budget_snapshot, check_reference, quiet_readback = prior.budget_snapshot, prior.check_reference, prior.quiet_readback
PRIOR_PREPARED = BOUNDED / 'execution_prepared_r04'
MODEL = BOUNDED / 'model_frozen_r01/frozen_model.json'
CASES = [dict(label=f'{mode}_{index}_{size}MiB', mode=mode, nbytes=size << 20,
              order_index=index, source_offset=0, operations=1)
         for mode in ('native', 'diagnostic') for index, size in enumerate((4, 16, 16, 4))]
PROTOCOL = dict(cases=CASES, executor='serial', chunk_bytes=32 << 20,
                necessary_pairs=[[2, 4], [4, 5]], source_bytes=16 << 20,
                source_worker=4, source_tier='hbm', target_worker=4, target_tier='hbm',
                target_allocation='fresh exact size', mode='cached', fits=0,
                size_repeats_per_mode=2, training_requests=0, model_updates=0,
                diagnostic_prepare='timing events created, recorded, device synchronized before execute request; extra preparation intervention',
                diagnostic_executor='expanded single local slice/copy/sync branch; native land dispatch checks bypassed; observer included',
                ordering_limit='native block precedes diagnostic block; time drift is not controlled')

def fresh_output(path):
    path = Path(path).resolve()
    require(path.parent == RESULTS and path.name.startswith('execution_'), 'fresh local slope execution output required')
    path.mkdir(parents=True, exist_ok=False)
    return path
