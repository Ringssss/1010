"""Recreate GPU transport processes while CPU owners retain every GLM backing block."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import resource
import shlex
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / 'main_lib'))
from fluidckpt.cluster.control import Cluster, request


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', type=Path, required=True)
    parser.add_argument('--phase', required=True)
    args = parser.parse_args()
    state = json.loads(args.session.read_text())
    out = args.session.parent / 'transport_recovery' / args.phase
    out.mkdir(parents=True, exist_ok=False)
    report = {'status': 'running', 'events': [], 'scope': 'GPU transport restart with independent CPU owners'}
    def event(name, **fields):
        row = {'time': time.time(), 'event': name, **fields}
        report['events'].append(row)
        (out / 'summary.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(row), flush=True)
    def remote(node, command, timeout=120):
        script = 'ulimit -c 0\nsource /opt/dtk/env.sh >/dev/null 2>&1\ncd ' + shlex.quote(str(ROOT))
        script += '\nexec ' + shlex.join(command)
        argv = ['docker', 'exec', state['nodes'][node]['container'], 'bash', '-lc', script]
        if node != 'node27':
            argv = ['ssh', '-o', 'BatchMode=yes', state['nodes'][node]['ip'], shlex.join(argv)]
        return subprocess.check_output(argv, text=True, stderr=subprocess.STDOUT, timeout=timeout)
    def owners():
        result = {}
        for node, socket_path in state['owner_sockets'].items():
            code = ('import json; from fluidckpt.memory.owner import call; '
                    f'print(json.dumps(call({socket_path!r}, "inventory")[0]))')
            result[node] = json.loads(remote(node, ['env', 'PYTHONPATH=' + str(ROOT / 'main_lib'),
                                                   'python3', '-c', code]))
        return result
    try:
        cluster = Cluster(state['endpoints'], state['profile'], planner_endpoint=state['endpoints'][0])
        workers, replicas = cluster.inventory(compact=True)
        if any(w['transactions'] for w in workers):
            raise RuntimeError('transport transactions must finish before recovery')
        wanted = set(state['source_ids'])
        before = owners()
        available = {r['key']: r for o in before.values() for r in o['regions'] if r['state'] == 'VALID'}
        expected = sum(s['n'] for s in state['sources'])
        if not wanted <= available.keys() or sum(available[k]['nbytes'] for k in wanted) != expected:
            raise RuntimeError('CPU owners must hold complete model coverage before transport restart')
        report['owner_before'] = before
        event('cpu_backing_verified', blocks=len(wanted), nbytes=expected)
        handles = [(node, Path(path)) for node, path in state['processes']
                   if Path(path).name.startswith('worker_')]
        if len(handles) != len(state['endpoints']):
            raise ValueError('session must identify each transport worker once')
        def stop(pair):
            node, spec = pair
            return remote(node, ['python3', 'codex_coding/src/task2/cluster/process.py',
                                 'stop', '--spec', str(spec)], timeout=60)
        with ThreadPoolExecutor(len(handles)) as pool:
            report['stopped'] = list(pool.map(stop, handles))
        event('gpu_workers_stopped')
        after_stop = owners()
        for node, owner in after_stop.items():
            if owner['pid'] != before[node]['pid'] or owner['used_bytes'] != before[node]['used_bytes']:
                raise RuntimeError('CPU owner identity or retained allocation changed')
        report['owner_after_stop'] = after_stop
        event('cpu_owners_preserved')
        replacements = []
        for node, old_path in handles:
            payload = json.loads(old_path.read_text())
            command = payload['command']
            command[command.index('--master') + 1] = '12.11.3.27:29802'
            payload['env'].update(TORCH_NCCL_ASYNC_ERROR_HANDLING='0', TORCH_NCCL_ENABLE_MONITORING='0')
            new_path = out / old_path.name
            payload['log'] = str(out / (old_path.stem + '.log'))
            new_path.write_text(json.dumps(payload, indent=2) + '\n')
            replacements.append((node, new_path))
        def start(pair):
            node, spec = pair
            return json.loads(remote(node, ['python3', 'codex_coding/src/task2/cluster/process.py',
                                           'start', '--spec', str(spec)]))
        with ThreadPoolExecutor(len(replacements)) as pool:
            report['started'] = list(pool.map(start, replacements))
        event('gpu_workers_started', count=len(replacements))
        state['processes'] = [pair for pair in state['processes'] if not Path(pair[1]).name.startswith('worker_')]
        state['processes'] += [(node, str(path)) for node, path in replacements]
        args.session.write_text(json.dumps(state, indent=2) + '\n')
        deadline = time.monotonic() + 180
        for endpoint in state['endpoints']:
            while True:
                try:
                    request(endpoint, 'status', timeout=3)
                    break
                except Exception:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(.2)
        cluster.call_all('mesh', [{} for _ in state['endpoints']])
        event('mesh_ready')
        report['adoption'] = cluster.call_all('adopt_owned', [{} for _ in state['endpoints']])
        workers, replicas = cluster.inventory(compact=True)
        published = {r['id']: r for r in replicas if r['state'] == 'VALID'}
        if not wanted <= published.keys() or sum(published[k]['n'] for k in wanted) != expected:
            raise RuntimeError('reconstructed directory lost complete model coverage')
        report['workers'] = [{k: w[k] for k in ['worker', 'epoch', 'used_bytes', 'hbm_allocated_bytes',
                                              'hbm_reserved_bytes', 'headroom_bytes']} for w in workers]
        report['status'] = 'passed'
        event('adopted_complete_model', blocks=len(wanted), nbytes=expected)
    except BaseException as exc:
        report.update(status='failed', error=repr(exc))
        event('failed_owners_retained', error=repr(exc))
        raise


if __name__ == '__main__':
    main()
