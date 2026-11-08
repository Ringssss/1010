"""Replace empty failed workers and rebuild transport while preserving live GLM backing."""
import argparse
import json
from pathlib import Path
import shlex
import subprocess

from run_system import ROOT
from fluidckpt.cluster.control import Cluster, request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', type=Path, required=True)
    args = parser.parse_args()
    state = json.loads(args.session.read_text())
    out = args.session.parent/'mesh_recovery'
    out.mkdir(exist_ok=False)
    reports = []
    master = '12.11.3.27:29801'
    processes = []
    worker_number = 0
    def remote(node, command, privileged=False):
        argv = ['docker', 'exec']+(['--privileged'] if privileged else [])
        argv += [state['nodes'][node]['container']]+command
        if node != 'node27':
            argv = ['ssh', '-o', 'BatchMode=yes', state['nodes'][node]['ip'], shlex.join(argv)]
        return subprocess.check_output(argv, text=True, stderr=subprocess.STDOUT, timeout=60)
    for node, spec_name in state['processes']:
        spec = Path(spec_name)
        if not spec.name.startswith('worker_'):
            processes.append([node, str(spec)])
            continue
        number = worker_number; worker_number += 1
        if number in [0, 20]:
            new_spec = out/spec.name
            payload = json.loads(spec.read_text())
            payload['command'][payload['command'].index('--master')+1] = master
            payload['env'].update(TORCH_NCCL_ASYNC_ERROR_HANDLING='0', TORCH_NCCL_ENABLE_MONITORING='0')
            payload['log'] = str(out/(spec.stem+'.log'))
            new_spec.write_text(json.dumps(payload)+'\n')
            result = remote(node, ['python3', str(ROOT/'codex_coding/src/task2/cluster/process.py'),
                                   'start', '--spec', str(new_spec)])
            processes.append([node, str(new_spec)])
        else:
            inventory = request(state['endpoints'][number], 'inventory?compact=1')
            if inventory['transactions']:
                raise RuntimeError('an active transaction prevents rebuilding the mesh')
            result = remote(node, ['python3', str(ROOT/'codex_coding/src/diag/reset_keeper_group.py'),
                '--pid-state', str(spec.with_suffix('.pid.json')), '--master', master,
                '--out', str(out/f'worker{number}.log')], privileged=True)
            processes.append([node, str(spec)])
        reports.append({'worker': number, 'node': node, 'result': result})
        (out/'progress.json').write_text(json.dumps(reports, indent=2)+'\n')
        print(json.dumps(reports[-1]), flush=True)
    state['processes'] = processes
    args.session.write_text(json.dumps(state, indent=2)+'\n')
    cluster = Cluster(state['endpoints'], state['profile'], planner_endpoint=state['endpoints'][0])
    cluster.call_all('mesh', [{} for _ in state['endpoints']])
    workers, replicas = cluster.inventory(compact=True)
    model = [row for row in replicas if row['id'].startswith('GLM/')]
    result = {'workers': len(workers), 'model_bytes': sum(row['n'] for row in model),
              'model_ranks': sorted({row['tp'] for row in model}), 'master': master}
    (out/'result.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
