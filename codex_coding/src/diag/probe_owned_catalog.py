"""Exercise real ClusterWorker allocation, publication and adoption after a client crash."""
import argparse
import json
import multiprocessing
import os
from pathlib import Path
import resource
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT/'main_lib'))
from fluidckpt.memory.owner import serve, call

resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
SIZE = 32 << 20


def make_worker(endpoint):
    os.environ['FLUIDCKPT_CPU_OWNER_SOCKET'] = endpoint
    from fluidckpt.cluster.worker import ClusterWorker
    worker = ClusterWorker(SimpleNamespace(node='node27', gpu=0, numa=1, session='owned-probe',
        dram_budget=64 << 20, hbm_budget=64 << 20, memory_backend='numa_registered'))
    worker.rank = 0
    return worker


def producer(endpoint):
    import torch
    from fluidckpt.serving.replicas import identity
    torch.set_num_threads(1)
    worker = make_worker(endpoint)
    meta = {'model_identity': 'owner-fixture-v1', 'tp_size': 1, 'tp_rank': 0, 'tensors': []}
    index = {'bytes': (0, SIZE)}
    desc = {'id': 'owned/block0', 'identity': identity(meta, index), 'tp': 0,
            'off': 0, 'n': SIZE, 'tier': 'dram', 'worker': 0, 'node': 'node27', 'gpu': 0,
            'numa': 1, 'epoch': worker.epoch, 'meta': meta, 'index': index}
    entry = worker.catalog.reserve(desc, lambda: worker.allocate(desc))
    expected = torch.arange(SIZE, device='cuda', dtype=torch.int32).remainder(251).to(torch.uint8)
    entry['tensor'].copy_(expected, non_blocking=True)
    torch.cuda.synchronize()
    worker.catalog.publish(desc['id'], True)
    os._exit(29)


def consumer(endpoint, output):
    import torch
    torch.set_num_threads(1)
    worker = make_worker(endpoint)
    adopted = worker.adopt_owned({})
    assert adopted['adopted'] == ['owned/block0']
    restored = worker.catalog.entries['owned/block0']['tensor'].to('cuda')
    expected = torch.arange(SIZE, device='cuda', dtype=torch.int32).remainder(251).to(torch.uint8)
    assert torch.equal(restored, expected)
    worker.catalog.retire('owned/block0')
    Path(output).write_text(json.dumps({'adopted': adopted, 'verified_bytes': SIZE})+'\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    endpoint = str(args.out/'owner.sock')
    ctx = multiprocessing.get_context('spawn')
    owner = ctx.Process(target=serve, args=(endpoint, 64 << 20)); owner.start()
    children = []
    report = {'scope': 'ClusterWorker with independent CPU ownership; synthetic bytes, real HIP copies'}
    try:
        deadline = time.monotonic()+30
        while not Path(endpoint).exists():
            if time.monotonic() > deadline: raise TimeoutError('owner startup')
            time.sleep(.05)
        for target, expected in [(producer, 29), (consumer, 0)]:
            call_args = (endpoint,) if target is producer else (endpoint, str(args.out/'adopted.json'))
            process = ctx.Process(target=target, args=call_args)
            children.append(process); process.start(); process.join(120)
            assert process.exitcode == expected, process.exitcode
            if target is producer:
                report['after_crash'] = call(endpoint, 'inventory')[0]
                assert report['after_crash']['used_bytes'] == SIZE
        report['after_retirement'] = call(endpoint, 'inventory')[0]
        assert report['after_retirement']['used_bytes'] == 0
        report.update(status='passed', verified_bytes=SIZE)
    except BaseException as exc:
        report.update(status='failed', error=repr(exc))
        raise
    finally:
        for child in children:
            if child.is_alive(): child.kill(); child.join(10)
        owner.terminate(); owner.join(10)
        Path(endpoint).unlink(missing_ok=True)
        (args.out/'result.json').write_text(json.dumps(report, indent=2)+'\n')
        print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
