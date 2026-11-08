"""Reuse immutable operations helpers with a single-worker admission contract."""
import importlib.util
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[6]
HERE = Path(__file__).resolve().parent
RESULTS = ROOT / 'codex_coding/results/v0.1.23.3/task2/cost_ledger'
PRIOR = ROOT / 'codex_coding/results/v0.1.23.3/task2/cost_ledger/execution_prepared_C1_r01'
ROLE_PRIOR = ROOT / 'codex_coding/results/v0.1.23.3/task2/first_op_validation/execution_prepared_A_r02'
sys.path.insert(0, str(ROOT / 'main_lib'))
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
previous = load('cost_ledger_prior_common', ROOT / 'codex_coding/src/v0.1.23.3/task2/bounded_cost/execution/common.py')
native, parent, ops, RUNTIME = previous.native, previous.parent, previous.ops, previous.RUNTIME
require, ref, check_reference, quiet_readback = previous.require, previous.ref, previous.check_reference, previous.quiet_readback
def write(path, body):
    # Mutable journals remain within a newly created run; archived run roots are never reused.
    Path(path).write_text(json.dumps(body, indent=2, sort_keys=True) + '\n')
def fresh_output(path):
    path = Path(path).resolve()
    require(path.parent == RESULTS and path.name.startswith('execution_'), 'new cost ledger execution output required')
    path.mkdir(parents=True, exist_ok=False)
    return path
def specs(directory):
    return [native.read(Path(directory) / 'node27.setup.spec.json')]
def check_setup(setup):
    import time
    require(setup['purpose'] == 'physical' and len(setup['workers']) == 1, 'single physical worker required')
    expected = setup['workers'][0]
    require((expected['worker'],expected['node'],expected['gpu'],expected['numa']) == (0,'node27',4,4), 'mapping changed')
    require(expected['actual_bdf'] == '0000:85:00.0', 'BDF changed')
    require(len(expected['container_id']) == 64 and expected['boot_id'] and isinstance(expected['pid_start_ticks'],int), 'exact physical identities missing')
    require(setup.get('ssd') == {} and setup['valid_until'] > time.time(), 'no SSD or expired setup')
    for reference in setup['evidence_refs'].values(): check_reference(reference)
def budget_snapshot(workers,setup,*,incremental_reserve=96 << 20,require_idle=True):
    require(len(workers)==1,'one worker required')
    w, expected = workers[0], setup['workers'][0]
    require((w['worker'],w['node'],w['gpu'],w['numa'],w['session']) == (0,'node27',4,4,setup['session']), 'runtime mapping/session changed')
    rt=w['runtime_identity']
    require(rt['pci_numa_node']==4,'physical PCI NUMA mapping changed')
    for key in ('boot_id','actual_bdf'):
        require(rt[key] == expected[key], 'runtime identity changed '+key)
    require(rt['start_ticks'] == expected['pid_start_ticks'] and rt['pid'] == expected['pid'], 'runtime process changed')
    require(w['staging_bytes']==64 << 20 and (not require_idle or not w['transactions']), 'staging or idle contract changed')
    dram=rt['cgroup_memory_current_bytes']; limit=rt['cgroup_memory_max_bytes']
    require(isinstance(limit,int) and limit<=8 << 30,'cgroup cap missing/above8GiB')
    upper=w['hbm_total_bytes']-w['headroom_bytes']['hbm']
    require(dram+(256 << 20)<=min(limit,6 << 30),'DRAM task cap/margin exhausted')
    require(upper+incremental_reserve+(256 << 20)<=2 << 30,'HBM absolute task cap/reserve/margin exhausted')
    require(w['headroom_bytes']['hbm']>=(2 << 30)+(32 << 20),'physical HBM reserve exhausted')
    return {'node27':dict(dram_current_bytes=dram,dram_limit_bytes=6 << 30,hbm_absolute_upper_bytes=upper,
        hbm_limit_bytes=2 << 30,planned_incremental_upper_bytes=incremental_reserve,application_margin_bytes=256 << 20,require_idle=require_idle,
        transactions=w['transactions'],peak_measured=False)}
