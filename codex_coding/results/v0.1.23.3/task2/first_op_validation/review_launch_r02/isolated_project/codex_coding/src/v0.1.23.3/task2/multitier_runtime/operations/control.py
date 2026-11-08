"""Parent-only finite setup/launch, unified STOP, and metadata collection."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[6]
HERE = Path(__file__).resolve().parent
RESULTS = ROOT / 'codex_coding/results/v0.1.23.3/task2/multitier_runtime'
SOURCE = ROOT / 'codex_coding/src/v0.1.23.3/task2/minimal_calibration/measure/orchestrate_integration.py'
ms = importlib.util.spec_from_file_location('original_operations_plumbing', SOURCE)
old = importlib.util.module_from_spec(ms)
ms.loader.exec_module(old)


def command(node, argv):
    return argv if node == 'node27' and socket.gethostname() == 'm03r2n07' else old.ssh(node, argv)


def specs(directory):
    return [json.loads((directory / (n + '.setup.spec.json')).read_text()) for n in ('node28', 'node30', 'node27')]


def readback(spec, out):
    metadata_spec = dict(spec, assets=[{k:v for k,v in a.items() if k != 'base64'} for a in spec.get('assets', [])])
    proc, stdout = old.record(command(spec['node'], ['python3', '-', json.dumps(metadata_spec)]), out, 35,
                              (HERE / 'host_readback.py').read_bytes())
    if proc['returncode'] != 0:
        raise RuntimeError('metadata readback failed ' + spec['node'])
    body = json.loads(stdout)
    old.write(out / 'stdout.json', body)
    if body['boot_id'] != spec['boot_id']:
        raise ValueError('boot changed; new freeze required')
    return body


def setup(args):
    if not args.execute:
        raise ValueError('parent setup requires --execute')
    args.out.mkdir(parents=True, exist_ok=False)
    wrapper = (args.setup_dir / 'setup_host.source.py').read_bytes()
    manifest = {'containers': {}, 'GPU_launches': 0}
    for spec in specs(args.setup_dir):
        node = spec['node']
        body = readback(spec, args.out / ('before_' + node))
        if not all(body['ports_free'].values()) or body['container'].get('Id'):
            raise ValueError('new name/ports no longer free')
        if not body['ssd_parent']['writable_executable']:
            raise ValueError('local SSD parent is not writable by current owner')
        bootstrap = '/tmp/fluidckpt_' + spec['run'] + '_bootstrap'
        import base64
        payload = {'wrapper': base64.b64encode(wrapper).decode(), 'spec': spec}
        destination = bootstrap + '/' + node + '.bootstrap_setup.py'
        upload = ['bash', '-c', 'set -C\nmkdir -p ' + shlex.quote(bootstrap) + '\ncat > ' + shlex.quote(destination)]
        process, _ = old.record(command(node, upload), args.out / ('bootstrap_' + node), 20,
                                 (HERE / 'bootstrap_setup.py').read_bytes())
        if process['returncode'] != 0:
            raise RuntimeError('fresh bootstrap source upload failed')
        remote = ['python3', destination, bootstrap, node, spec['boot_id']]
        process, raw = old.record(command(node, remote), args.out / ('setup_' + node), 45, json.dumps(payload).encode())
        if raw:
            old.write(args.out / ('setup_' + node) / 'stdout.json', json.loads(raw))
        if process['returncode'] != 0:
            raise RuntimeError('setup failed; preserve raw result and inspect exact new container')
        after = readback(spec, args.out / ('after_' + node))
        ci = after['container']
        hc = ci['HostConfig']
        if not ci['State']['Running'] or ci['Image'] != spec['image_id'] or hc['Memory'] != 8 << 30:
            raise ValueError('new container hard limit/image/running mismatch')
        if hc['Privileged'] or hc['IpcMode'] != 'private' or not {'IPC_LOCK', 'SYS_NICE'} <= set(hc['CapAdd']):
            raise ValueError('new container permissions mismatch')
        mounts = {m['Destination']: m['Source'] for m in ci['Mounts']}
        if mounts != {str(ROOT): spec['stage'] + '/project', '/task_ssd': spec['ssd_directory'], '/opt/hyhal': '/opt/hyhal'}:
            raise ValueError('unexpected mount set')
        if after['cgroup']['files']['pids.current'] != '1' or not all(after['source_assets'].get(a['relative_path']) == a['sha256'] for a in spec['assets']):
            raise ValueError('stage hash or quiet setup differs')
        manifest['containers'][node] = {'container_id': ci['Id'], 'container_started_at': ci['State']['StartedAt'], 'boot_id': after['boot_id']}
        old.write(args.out / 'setup.json', manifest)
    print(json.dumps({'out': str(args.out), 'containers': manifest['containers'], 'GPU_launches': 0}))


def stage(args):
    if not args.execute:
        raise ValueError('parent source deployment requires --execute')
    args.out.mkdir(parents=True, exist_ok=False)
    import base64
    bound = json.loads(args.bindings.read_text())['containers']
    assets = []
    paths = set((ROOT/'main_lib/fluidckpt').rglob('*.py'))
    for directory in (HERE, HERE.parent/'runner', ROOT/'codex_coding/src/v0.1.23.3/task2/multitier_scenarios/adapter'):
        paths.update(directory.rglob('*.py'))
    paths.add(ROOT/'codex_coding/src/v0.1.23.3/task2/three_node_recovery/admission28/runtime_gate_r03.py')
    for path in sorted(paths):
        raw = path.read_bytes()
        assets.append({'relative_path':str(path.relative_to(ROOT)), 'sha256':hashlib.sha256(raw).hexdigest(), 'base64':base64.b64encode(raw).decode()})
    old.write(args.out/'source_manifest.json', {'assets':[{k:v for k,v in x.items() if k != 'base64'} for x in assets]})
    for spec in specs(args.setup_dir):
        node = spec['node']
        spec.update(bound[node])
        before = readback(spec, args.out/('before_'+node))
        if before['cgroup']['files']['pids.current'] != '1':
            raise ValueError('source deployment requires quiet task container')
        target = '/tmp/fluidckpt_'+spec['run']+'_bootstrap/'+node+'.'+args.out.name+'.stage_assets.py'
        upload = ['bash','-c','set -C\ncat > '+shlex.quote(target)]
        proc,_ = old.record(command(node,upload),args.out/('upload_'+node),15,(HERE/'stage_assets.py').read_bytes())
        if proc['returncode'] != 0:
            raise RuntimeError('source deployment script upload failed')
        package = {'spec':spec,'revision':args.out.name,'assets':assets}
        proc,raw = old.record(command(node,['python3',target]),args.out/('stage_'+node),30,json.dumps(package).encode())
        if proc['returncode'] != 0:
            raise RuntimeError('source deployment failed')
        old.write(args.out/('stage_'+node)/'stdout.json',json.loads(raw))
        previous = {x['relative_path']:x for x in spec['assets']}
        previous.update({x['relative_path']:x for x in assets})
        spec['assets'] = list(previous.values())
        old.write(args.out/(node+'.setup.spec.json'),spec)
        after = readback(spec,args.out/('after_'+node))
        if any(after['source_assets'].get(x['relative_path']) != x['sha256'] for x in spec['assets']):
            raise ValueError('final staged source SHA differs')
    old.write(args.out/'manifest.json', {'GPU_launches':0,'source_manifest_ref':old.ref(args.out/'source_manifest.json'),
              'new_setup_directory':str(args.out),'bindings_ref':old.ref(args.bindings)})
    print(json.dumps({'setup_directory':str(args.out),'GPU_launches':0}))


def bind(args):
    args.out.mkdir(parents=True, exist_ok=False)
    bound = json.loads(args.bindings.read_text())['containers']
    processes, workers, stores = [], [], {}
    mesh = 'node28:0-1,node30:4-5,node27:0-0,node27:4-4'
    session = specs(args.setup_dir)[0]['run']
    for s in specs(args.setup_dir):
        n = s['node']
        c = bound[n]
        base = {'node': n, **c, 'env': {'PYTHONPATH': str(ROOT / 'main_lib'), 'PYTHONDONTWRITEBYTECODE': '1', 'OMP_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1'},
                'unset_env': ['HIP_VISIBLE_DEVICES', 'CUDA_VISIBLE_DEVICES', 'ROCR_VISIBLE_DEVICES']}
        for role in s['roles']:
            rank = len(workers)
            gpu = role['gpu']
            key = 'w' + str(rank)
            row = dict(base, key=key, kind='worker', gpu=gpu, numa=role['numa'], bdf=role['bdf'], worker=rank,
                       endpoint=f'http://12.11.3.{n[-2:]}:{35100+gpu}', out=str(RESULTS / f'operations_process_{session}_{key}'))
            row['env'] = dict(base['env'], HSA_FORCE_FINE_GRAIN_PCIE='1', NCCL_NET_PLUGIN='librccl-net-shca.so',
                NCCL_NET='IBext_v8', NCCL_IB_HCA='shca_0', NCCL_SOCKET_IFNAME='ib0', NCCL_TOPO_FILE=s['topology_input'],
                NCCL_DEBUG='INFO', NCCL_DEBUG_SUBSYS='INIT,GRAPH,NET', NCCL_TOPO_DUMP_FILE_RANK='1' if rank in (3,5) else '0',
                NCCL_TOPO_DUMP_FILE=str(RESULTS / f'operations_dump_{session}' / (key+'.runtime.dump.xml')))
            row['argv'] = ['python3', str(HERE.parent / 'runner/runtime_worker.py'), '--node', n, '--gpu', str(gpu), '--numa', str(role['numa']),
                '--port-base', '35100', '--mesh', mesh, '--master', '12.11.3.28:29981', '--handoff', str(ROOT/'handoff_own_session'),
                '--session', session, '--dram-budget', str(3 << 30), '--hbm-budget', str((1 << 30)-(64 << 20))]
            processes.append(row)
            workers.append({k: row[k] for k in ('worker','node','gpu','numa','bdf','endpoint','container_id','boot_id')})
        key = 'ssd_' + s['logical_node'].lower()
        row = dict(base, key=key, kind='ssd', out=str(RESULTS / f'operations_process_{session}_{key}'))
        row['argv'] = ['python3', '-m', 'fluidckpt.serving.ssd_store', '--directory', '/task_ssd', '--port', str(s['ssd_port'])]
        processes.append(row)
        stores[s['logical_node'].lower()] = {'endpoint': f'http://12.11.3.{n[-2:]}:{s["ssd_port"]}', 'storage_node': n,
            'directory': s['ssd_directory'], 'container_directory': '/task_ssd', 'backing_evidence_ref': old.ref(args.bindings)}
    plan = dict(session=session, processes=processes, workers=workers, ssd=stores,
                startup_s=90, lifetime_s=1800, controller_timeout_s=1440,
                aggregate_budget={'dram':6 << 30,'hbm':2 << 30,'application_margin':256 << 20},
                setup_directory=str(args.setup_dir), bindings_ref=old.ref(args.bindings), physical_ready=False,
                dump_scope='per-worker necessary pair communicator observation; w3/w5 rank1, others rank0; not proof of every pair')
    old.write(args.out / 'launch_plan.json', plan)
    print(json.dumps({'plan':str(args.out / 'launch_plan.json'),'GPU_launches':0}))


def stops(plan, digest, out):
    result = {}
    def one(row):
        argv = command(row['node'], ['docker', 'exec', '-i', row['container_id'], 'tee', row['out'] + '/STOP.json'])
        try:
            p, _ = old.record(argv, out / ('stop_' + row['key']), 12, (json.dumps({'plan_sha256': digest})+'\n').encode())
            return row['key'], p
        except BaseException as exc:
            return row['key'], {'failure': repr(exc)}
    with ThreadPoolExecutor(max_workers=9) as pool:
        for key, process in pool.map(one, plan['processes']):
            result[key] = process
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument('mode', choices=['readonly', 'setup', 'stage', 'bind'])
    p.add_argument('--setup-dir', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--bindings', type=Path)
    p.add_argument('--execute', action='store_true')
    a = p.parse_args()
    a.setup_dir, a.out = a.setup_dir.resolve(), a.out.resolve()
    if a.out.parent != RESULTS or not a.out.name.startswith('operations_'):
        raise ValueError('fresh owned operations output required')
    if a.mode == 'readonly':
        a.out.mkdir(parents=True, exist_ok=False)
        with ThreadPoolExecutor(max_workers=3) as pool:
            bodies = list(pool.map(lambda s: readback(s, a.out/s['node']), specs(a.setup_dir)))
        old.write(a.out/'manifest.json', {'nodes':[b['node'] for b in bodies], 'ports_free':all(all(b['ports_free'].values()) for b in bodies), 'GPU_launches':0,'remote_writes':0})
    elif a.mode == 'setup':
        setup(a)
    elif a.mode == 'stage':
        stage(a)
    else:
        bind(a)


if __name__ == '__main__':
    main()
