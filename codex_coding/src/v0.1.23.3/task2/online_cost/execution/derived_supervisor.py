"""Bound owned workers/services using the verified exact-owner cleanup helper."""
import argparse
import ctypes
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


def enable_subreaper():
    libc=ctypes.CDLL(None,use_errno=True)
    if libc.prctl(36,1,0,0,0)!=0:
        raise OSError(ctypes.get_errno(),'PR_SET_CHILD_SUBREAPER failed')
    flag=ctypes.c_int()
    if libc.prctl(37,ctypes.byref(flag),0,0,0)!=0 or flag.value!=1:
        raise RuntimeError('child subreaper verification failed')
    return True


def reap_adopted_children(timeout_s=2):
    # This supervisor creates only its exact task child; waitpid cannot reap
    # another supervisor's children and this function sends no signals.
    deadline=time.monotonic()+timeout_s
    reaped=[]
    while True:
        try:
            pid,status=os.waitpid(-1,os.WNOHANG)
        except ChildProcessError:
            return dict(passed=True,reaped=reaped,pending_children=[],waitpid_echild=True,scope='only direct/adopted children of this task supervisor; no signals')
        if pid:
            reaped.append(dict(pid=pid,wait_status=status))
            continue
        if time.monotonic()>=deadline:
            children=Path('/proc/self/task')/str(os.getpid())/'children'
            return dict(passed=False,reaped=reaped,pending_children=children.read_text().split(),scope='bounded wait for own adopted children; no signals')
        time.sleep(.025)


def final_owned_closure(gate, pid, owner, boot, namespace, cleanup, reaping):
    # This is a read-only final ledger. It never retries a rejected signal.
    registered = (all(key in owner for key in gate.IDENTITY)
                  and owner['pid'] == owner['pgid'] == owner['sid'] == pid)
    boot_unchanged = gate.boot_id() == boot
    namespace_unchanged = os.readlink('/proc/self/ns/pid') == namespace
    if not registered or not boot_unchanged or not namespace_unchanged:
        return dict(passed=False,registered_owner_complete=registered,
                    boot_unchanged=boot_unchanged,pid_namespace_unchanged=namespace_unchanged,
                    reason='final closure scope identity differs; no process inspection or signal')
    leader = gate.proc(pid)
    members = gate.group_members(owner['pgid'])
    reaped = [row for row in reaping.get('reaped', []) if row['pid'] == pid]
    terminal = (len(reaped) == 1 and
                (os.WIFEXITED(reaped[0]['wait_status']) or os.WIFSIGNALED(reaped[0]['wait_status'])))
    # A direct child's terminal waitpid record refers to the registered child;
    # that PID cannot have been reused while the child remained unreaped.
    race = (not cleanup.get('passed')
            and cleanup.get('reason') == 'identity changed before KILL; no further signal'
            and registered and gate.exact(cleanup.get('before', {}), owner)
            and terminal)
    facts = dict(registered_owner_complete=registered,
                 boot_unchanged=boot_unchanged,
                 pid_namespace_unchanged=namespace_unchanged,
                 leader_after=leader, pending_group_members=members,
                 waitpid_echild=bool(reaping.get('waitpid_echild')),
                 original_guard_passed=bool(cleanup.get('passed')),
                 registered_leader_wait_record=reaped,
                 guard_exit_race_reconciled=race,
                 scope='final exact owned group and supervisor child closure; read-only; no additional signals')
    facts['passed'] = bool(registered and facts['boot_unchanged']
                           and facts['pid_namespace_unchanged']
                           and leader.get('state') == 'absent' and not members
                           and reaping.get('passed') and facts['waitpid_echild']
                           and (cleanup.get('passed') or race))
    if terminal:
        facts['registered_leader_exitcode'] = os.waitstatus_to_exitcode(reaped[0]['wait_status'])
    return facts


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
        summary['child_subreaper_enabled']=enable_subreaper()
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
        if not (cg / 'memory.current').exists():
            cg = Path('/sys/fs/cgroup')
        if not (cg / 'memory.current').exists() or not (cg / 'memory.max').exists():
            raise ValueError('shared cgroup v2 memory counters unavailable')
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
            meminfo = Path('/proc/meminfo').read_text()
            available = next(int(line.split()[1]) * 1024 for line in meminfo.splitlines() if line.startswith('MemAvailable:'))
            sample['host_mem_available_bytes'] = available
            summary['samples'].append(sample)
            if available < plan['nodes'][row['node']]['ongoing_host_available_floor_bytes']:
                summary['stop_reason'] = 'host_memory_reserve_breached'
                gate.write(out / 'ALARM.json', {'plan_sha256': digest, 'reason': summary['stop_reason'], 'sample': sample})
                print(json.dumps({'key': a.key, 'alarm': summary['stop_reason']}), flush=True)
                break
            current = sample['memory.current']
            if not isinstance(current, str) or not current.isdigit():
                raise ValueError('shared cgroup memory.current unavailable')
            if isinstance(current, str) and current.isdigit() and int(current) > plan['aggregate_budget_by_node'][row['node']]['dram']:
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
            try:
                summary['adopted_child_reap']=reap_adopted_children()
            except BaseException as exc:
                summary['adopted_child_reap']={'passed':False,'error':repr(exc)}
            try:
                summary['final_owned_closure']=final_owned_closure(
                    gate,child.pid,owner,boot,ns,summary['cleanup'],summary['adopted_child_reap'])
            except BaseException as exc:
                summary['final_owned_closure']={'passed':False,'error':repr(exc)}
        summary['elapsed_s'] = time.monotonic() - start
        summary['passed'] = bool(not summary.get('failure') and summary.get('stop_reason') == 'owned_stop_marker'
                                 and summary.get('final_owned_closure', {}).get('passed'))
        gate.write(out / 'supervisor.summary.json', summary)
    print(json.dumps({'key': a.key, 'passed': summary['passed']}), flush=True)
    raise SystemExit(0 if summary['passed'] else 2)


if __name__ == '__main__':
    main()
