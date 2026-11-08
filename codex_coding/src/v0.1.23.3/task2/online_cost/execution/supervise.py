"""Reuse the established owned-role supervisor in a new online experiment root."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[6]
RESULTS = ROOT / 'codex_coding/results/v0.1.23.3/task2/online_cost'
SOURCE = Path(__file__).with_name('derived_supervisor.py')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--plan', type=Path, required=True)
    p.add_argument('--key', required=True)
    p.add_argument('--execute', action='store_true')
    a = p.parse_args()
    provenance = json.loads(SOURCE.with_name('supervisor_provenance.json').read_text())
    import hashlib
    if hashlib.sha256(SOURCE.read_bytes()).hexdigest() != provenance['derived_sha256']:
        raise ValueError('derived supervisor source SHA differs')
    plan = json.loads(a.plan.read_text())
    row = next(r for r in plan['processes'] if r['key'] == a.key)
    out = Path(row['out']).resolve()
    parent = out.parent
    if parent.parent != RESULTS or not parent.name.startswith('execution_runtime_') or not out.name.startswith('operations_process_'):
        raise ValueError('new owned online execution_runtime_* result root required')
    if plan.get('schema') != 'online_cost_launch.v1':
        raise ValueError('unsupported online launch plan')
    if not a.execute:
        print(json.dumps({'status': 'launch_not_executed', 'key': a.key, 'argv': row['argv'],
                          'out': str(out), 'requires': 'fresh bound admission plus main approval'}))
        return
    if plan.get('main_approved') is not True or plan.get('fresh_admission_passed') is not True:
        raise ValueError('main must bind fresh hardware and explicit real-model budget before execution')
    if not row.get('container_id') or not row.get('boot_id'):
        raise ValueError('exact fresh container and boot binding required')
    if any('__FRESH_' in value for value in row['argv']):
        raise ValueError('all physical role bindings must be fresh and concrete')
    if not 0 < plan.get('lifetime_s', 0) <= 7200:
        raise ValueError('bounded lifetime required')
    source = importlib.util.spec_from_file_location('online_owned_supervisor', SOURCE)
    supervisor = importlib.util.module_from_spec(source)
    source.loader.exec_module(supervisor)
    supervisor.RESULTS = parent
    operational = plan['aggregate_budget_by_node'][row['node']]['dram']
    print(json.dumps({'effective_operational_dram_bytes': operational, 'node': row['node'], 'key': a.key}), flush=True)
    if row['kind'] == 'engine':
        dump = Path(row['env']['NCCL_TOPO_DUMP_FILE'])
        if dump.exists() or dump == Path(row['env']['NCCL_TOPO_FILE']):
            raise ValueError('engine dump must be new and distinct from topology input')
        dump.parent.mkdir(parents=True, exist_ok=True)
    sys.argv = [str(SOURCE), '--plan', str(a.plan), '--key', a.key]
    supervisor.main()


if __name__ == '__main__':
    main()
