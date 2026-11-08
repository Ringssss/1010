"""Bound owned workers/services using the verified exact-owner cleanup helper."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import resource
import signal
import subprocess
import time

ROOT = Path(__file__).resolve().parents[6]
RESULTS = ROOT / 'codex_coding/results/v0.1.23.3/task2/multitier_runtime'
HELPER = ROOT / 'codex_coding/src/v0.1.23.3/task2/three_node_recovery/admission28/runtime_gate_r03.py'
HELPER_SHA = 'd1ae5e31c3bf801d36011cbb9a6e911b400fd4e64c6689c8c742d4aa78d4f058'


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--plan', type=Path, required=True)
    p.add_argument('--key', required=True)
    a = p.parse_args()
    plan = json.loads(a.plan.read_text())
    row = next(r for r in plan['processes'] if r['key'] == a.key)
    if hashlib.sha256(HELPER.read_bytes()).hexdigest() != HELPER_SHA:
        raise ValueError('frozen cleanup helper differs')
    spec = importlib.util.spec_from_file_location('frozen_exact_cleanup', HELPER)
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    if gate.boot_id() != row['boot_id'] or not row.get('container_id'):
        raise ValueError('current boot/container binding missing')
    out = Path(row['out'])
    if out.parent != RESULTS or not out.name.startswith('operations_process_'):
        raise ValueError('owned output required')
    out.mkdir(parents=True, exist_ok=False)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    env = dict(os.environ, **row['env'])
    for key in row['unset_env']:
        env.pop(key, None)
    if row['kind'] == 'worker':
        dump = Path(env['NCCL_TOPO_DUMP_FILE'])
        if dump.exists() or dump == Path(env['NCCL_TOPO_FILE']):
            raise ValueError('fresh dump distinct from input required')
        dump.parent.mkdir(parents=True, exist_ok=True)
    boot, ns = gate.boot_id(), os.readlink('/proc/self/ns/pid')
    digest = hashlib.sha256(a.plan.read_bytes()).hexdigest()
    stop = {'reason': None}
    def requested(signum, frame):
        stop['reason'] = 'supervisor_signal_' + str(signum)
    signal.signal(signal.SIGTERM, requested)
    signal.signal(signal.SIGINT, requested)
    start, child, owner = time.monotonic(), None, {}
    summary = dict(key=a.key, plan_sha256=digest, boot_id=boot, pid_namespace=ns,
                   container_id=row['container_id'], samples=[], source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    try:
        with (out / 'child.log').open('xb') as log:
            child = subprocess.Popen(row['argv'], env=env, cwd=ROOT, stdout=log,
                                     stderr=subprocess.STDOUT, start_new_session=True)
        owner = gate.proc(child.pid)
        if not owner.get('live') or owner['argv'][1:] != row['argv'][1:] or owner['pgid'] != child.pid or owner['sid'] != child.pid:
            raise ValueError('exact child registration failed')
        gate.write(out / 'child.owner.json', dict(owner, boot_id=boot, pid_namespace=ns, plan_sha256=digest))
        print(json.dumps({'key': a.key, 'child_pid': child.pid}), flush=True)
        raw = Path('/proc/self/cgroup').read_text().strip().split('::', 1)[1]
        cg = Path('/sys/fs/cgroup') / raw.lstrip('/')
        summary['cgroup_path'] = str(cg)
        while child.poll() is None:
            elapsed = time.monotonic() - start
            marker = out / 'STOP.json'
            if marker.exists():
                if json.loads(marker.read_text()).get('plan_sha256') != digest:
                    raise ValueError('STOP belongs to another plan')
                stop['reason'] = 'owned_stop_marker'
            if stop['reason'] or elapsed > plan['lifetime_s']:
                summary['stop_reason'] = stop['reason'] or 'lifetime_deadline'
                break
            sample = {'elapsed_s': elapsed}
            for name in ('memory.current', 'memory.peak', 'memory.max', 'memory.events', 'pids.current'):
                try:
                    sample[name] = (cg / name).read_text().strip()
                except OSError as exc:
                    sample[name] = {'error': repr(exc)}
            summary['samples'].append(sample)
            current = sample['memory.current']
            if isinstance(current, str) and current.isdigit() and int(current) > plan['aggregate_budget']['dram']:
                summary['stop_reason'] = 'aggregate_task_dram_cap_exceeded'
                gate.write(out / 'ALARM.json', {'plan_sha256': digest, 'reason': summary['stop_reason'], 'sample': sample})
                print(json.dumps({'key': a.key, 'alarm': summary['stop_reason']}), flush=True)
                break
            time.sleep(.25)
        if child.poll() is not None and not summary.get('stop_reason'):
            summary['failure'] = 'child exited before coordinated STOP'
    except BaseException as exc:
        summary['failure'] = repr(exc)
    finally:
        if child is not None:
            try:
                summary['cleanup'] = gate.cleanup(child, owner, boot, ns)
            except BaseException as exc:
                summary['cleanup'] = {'passed': False, 'error': repr(exc)}
            summary['child_returncode'] = child.poll()
        summary['elapsed_s'] = time.monotonic() - start
        summary['passed'] = bool(not summary.get('failure') and summary.get('stop_reason') == 'owned_stop_marker'
                                 and summary.get('cleanup', {}).get('passed'))
        gate.write(out / 'supervisor.summary.json', summary)
    print(json.dumps({'key': a.key, 'passed': summary['passed']}), flush=True)
    raise SystemExit(0 if summary['passed'] else 2)


if __name__ == '__main__':
    main()
