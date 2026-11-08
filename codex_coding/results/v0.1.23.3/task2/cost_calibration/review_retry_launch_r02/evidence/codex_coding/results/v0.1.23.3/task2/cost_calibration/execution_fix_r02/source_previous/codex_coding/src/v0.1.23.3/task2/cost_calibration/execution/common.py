"""Thin access to frozen native coordinator and task-owned operations."""
import importlib.util
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[6]
HERE = Path(__file__).resolve().parent
RESULTS = ROOT/'codex_coding/results/v0.1.23.3/task2/cost_calibration'
sys.path.insert(0, str(ROOT/'main_lib'))
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
RUNTIME = ROOT/'codex_coding/src/v0.1.23.3/task2/multitier_runtime'
native = load('costcal_native_coordinator', RUNTIME/'runner/run.py')
parent = load('costcal_old_parent', RUNTIME/'operations/run_session.py')
ops = parent.ops
write, ref, require = native.write, native.ref, native.require

def budget_snapshot(workers, setup, *, incremental_reserve=64 << 20):
    native.check_workers(workers, setup)
    nodes = {}
    for node in ('node28', 'node30', 'node27'):
        group = [w for w in workers if w['node'] == node]
        dram = max(w['runtime_identity']['cgroup_memory_current_bytes'] for w in group)
        limit = min(w['runtime_identity']['cgroup_memory_max_bytes'] for w in group)
        # All selected-device used values come from this same HIP API/sample family.
        upper = sum(w['hbm_total_bytes']-w['headroom_bytes']['hbm'] for w in group)
        require(dram+(256 << 20) <= min(limit, 6 << 30), 'fresh DRAM reserve exhausted '+node)
        require(upper+incremental_reserve <= 2 << 30, 'same-HIP absolute HBM upper bound/reserve exhausted '+node)
        require(all(w['headroom_bytes']['hbm'] >= (2 << 30)+(32 << 20) for w in group), 'physical HBM reserve exhausted '+node)
        nodes[node] = dict(dram_current_bytes=dram, dram_limit_bytes=6 << 30,
            hbm_absolute_upper_bytes=upper, hbm_limit_bytes=2 << 30,
            remaining_hbm_bytes=(2 << 30)-upper, planned_incremental_upper_bytes=incremental_reserve,
            hbm_scope='same-HIP selected-device absolute total-free; includes other allocations conservatively', peak_measured=False)
    return nodes
