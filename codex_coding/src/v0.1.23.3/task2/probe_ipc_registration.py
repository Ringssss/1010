"""Isolate IPC import against registered-host-memory residency with fresh exporters.

Uses real storage sizes, not a model forward pass. Three conditions: small
registered cache, large registered base, then base released again.
"""
import argparse
import gc
import json
import multiprocessing as mp
from pathlib import Path
import pickle
import resource
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT/'main_lib'))


def exporter(template_path, gpu, output, channel):
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    import torch
    torch.set_num_threads(1); torch.cuda.set_device(gpu)
    with open(template_path, 'rb') as f: template = pickle.load(f)
    tensors, entries = [], []
    for i, entry in enumerate(template['entries']):
        tensor = torch.empty(entry['nbytes'], dtype=torch.uint8, device=f'cuda:{gpu}')
        tensor[0] = i % 251; tensor[-1] = i % 251
        tensors.append(tensor)
        entries.append(dict(entry, handle=tensor.untyped_storage()._share_cuda_()))
    torch.cuda.synchronize()
    with open(output, 'wb') as f: pickle.dump({'meta': template['meta'], 'entries': entries}, f)
    channel.send({'ready': True, 'storages': len(entries), 'bytes': sum(e['nbytes'] for e in entries)})
    if not channel.poll(180): raise TimeoutError('importer did not release storages')
    assert channel.recv() == 'release'


def main(args):
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    args.out.mkdir(parents=True, exist_ok=False)
    raw = subprocess.check_output(['hy-smi', '--showmeminfo', 'vram', '--json'], text=True)
    memory = json.loads(raw[raw.index('{'):])
    if int(memory[f'card{args.gpu}']['vram Total Used Memory (MiB)']) > 1024:
        raise RuntimeError('selected GPU is occupied')
    import torch
    from fluidckpt.memory import HostAllocation
    from fluidckpt.serving.keeper import HbmTarget
    from fluidckpt.serving.prepare_trace import PrepareTrace
    torch.set_num_threads(1); torch.cuda.set_device(args.gpu)
    with open(args.template, 'rb') as f: template = pickle.load(f)
    index, offset = {}, 0
    for entry in template['entries']:
        index[entry['name']] = (offset, entry['nbytes']); offset += entry['nbytes']
    if torch.cuda.mem_get_info(args.gpu)[0] < offset+(6 << 30):
        raise MemoryError('not enough GPU capacity for storage-shape replay')
    cache = HostAllocation(1 << 30, 'numa_registered', args.numa)
    hbm_cache = torch.empty(4 << 30, dtype=torch.uint8, device=f'cuda:{args.gpu}')
    big = None
    report = {'gpu': args.gpu, 'numa': args.numa, 'storage_count': len(index), 'bytes': offset,
              'samples': [], 'scope': 'fresh same-size exporters, one importer; no inference, no complete weight validation'}
    def sample(name):
        parent, child = mp.get_context('spawn').Pipe()
        exported = args.out/f'{name}.pkl'
        process = mp.get_context('spawn').Process(target=exporter, args=(args.template, args.gpu, exported, child))
        process.start(); target = None
        try:
            if not parent.poll(120): raise TimeoutError('exporter not ready')
            info = parent.recv(); assert info['ready']
            trace = PrepareTrace(args.out/'profiles', args.gpu, name)
            started = time.monotonic()
            try:
                target = HbmTarget(exported, index, args.gpu, template['meta'], trace=trace)
                elapsed = time.monotonic()-started
            finally:
                trace.finish()
            values = torch.stack([v for _, _, tensor in target.segs for v in (tensor[0], tensor[-1])]).cpu().tolist()
            assert values == [i % 251 for i in range(len(index)) for _ in range(2)]
            del values; target = None
            gc.collect(); torch.cuda.synchronize(); torch.cuda.empty_cache()
            parent.send('release'); process.join(30)
            if process.exitcode != 0: raise RuntimeError(f'exporter failed: {process.exitcode}')
            profile = json.loads((args.out/'profiles'/f'worker{args.gpu}_{name}.json').read_text())
            imports = [r for r in profile['rows'] if r['stage'] == 'ipc_import']
            row = {'condition': name, 'mapping_s': elapsed, 'ipc_wall_s': sum(r['wall_s'] for r in imports),
                   'ipc_cpu_s': sum(r['thread_cpu_s'] for r in imports), 'ipc_max_s': max(r['wall_s'] for r in imports),
                   'sampled_bytes_correct': True, 'registered_bytes': (1 << 30)+(offset if big else 0)}
            report['samples'].append(row)
            (args.out/'summary.json').write_text(json.dumps(report, indent=2))
            print(json.dumps(row), flush=True)
        finally:
            target = None
            if process.is_alive(): process.terminate(); process.join(10)
            if process.is_alive(): process.kill(); process.join(10)
            parent.close(); child.close()
    try:
        sample('small_cache')
        big = HostAllocation(offset, 'numa_registered', args.numa)
        sample('large_base_registered')
        big.close(); big = None
        sample('large_base_released')
        report['passed'] = True
    finally:
        if big: big.close()
        cache.close(); del hbm_cache
        torch.cuda.empty_cache()
        (args.out/'summary.json').write_text(json.dumps(report, indent=2))


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--template', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True); p.add_argument('--gpu', type=int, required=True)
    p.add_argument('--numa', type=int, required=True)
    main(p.parse_args())
