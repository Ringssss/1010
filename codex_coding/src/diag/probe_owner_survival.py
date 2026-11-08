"""Verify that a CPU-owned region survives an abrupt GPU transport-process exit."""
import argparse
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import resource
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT/'main_lib'))
from fluidckpt.memory.owner import Mapping, call, serve

resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
SIZE = 32 << 20


def writer(endpoint):
    import torch
    torch.set_num_threads(1)
    torch.cuda.set_device(0)
    mapping = Mapping(endpoint, 'checkpoint/block0', SIZE, numa=1,
                      metadata={'model_version': 'synthetic-v1', 'off': 0, 'n': SIZE})
    tensor = torch.frombuffer(mapping.map, dtype=torch.uint8, count=SIZE)
    rc = torch.cuda.cudart().cudaHostRegister(tensor.data_ptr(), mapping.info['reserved'], 0)
    if int(rc): raise RuntimeError(f'host registration failed: {rc}')
    value = torch.arange(SIZE, dtype=torch.int32, device='cuda').remainder(251).to(torch.uint8)
    tensor.copy_(value, non_blocking=True)
    torch.cuda.synchronize()
    mapping.publish()
    # Deliberately bypass all Python/CUDA cleanup as a failed transport process would.
    os._exit(23)


def reader(endpoint, result_path):
    import torch
    torch.set_num_threads(1)
    torch.cuda.set_device(0)
    mapping = Mapping(endpoint, 'checkpoint/block0')
    tensor = torch.frombuffer(mapping.map, dtype=torch.uint8, count=SIZE)
    rc = torch.cuda.cudart().cudaHostRegister(tensor.data_ptr(), mapping.info['reserved'], 0)
    if int(rc): raise RuntimeError(f'host registration failed: {rc}')
    restored = tensor.to('cuda', non_blocking=True)
    torch.cuda.synchronize()
    expected = torch.arange(SIZE, dtype=torch.int32).remainder(251).to(torch.uint8)
    assert torch.equal(restored.cpu(), expected)
    digest = hashlib.sha256(tensor.numpy()).hexdigest()
    torch.cuda.cudart().cudaHostUnregister(tensor.data_ptr())
    del tensor
    mapping.close()
    Path(result_path).write_text(json.dumps({'verified_bytes': SIZE, 'sha256': digest})+'\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    endpoint = args.out/'owner.sock'
    context = multiprocessing.get_context('spawn')
    owner = context.Process(target=serve, args=(str(endpoint), 64 << 20))
    owner.start()
    children = []
    report = {'scope': '32MiB synthetic checkpoint; real HIP D2H/H2D; transport process deliberately exits'}
    try:
        deadline = time.monotonic()+30
        while not endpoint.exists():
            if time.monotonic() > deadline: raise TimeoutError('owner did not start')
            time.sleep(.05)
        producer = context.Process(target=writer, args=(str(endpoint),))
        children.append(producer); producer.start(); producer.join(120)
        assert producer.exitcode == 23, producer.exitcode
        report['after_transport_exit'] = call(endpoint, 'inventory')[0]
        assert owner.is_alive()
        assert report['after_transport_exit']['used_bytes'] == SIZE
        assert report['after_transport_exit']['regions'][0]['state'] == 'VALID'
        consumer = context.Process(target=reader, args=(str(endpoint), str(args.out/'reader.json')))
        children.append(consumer); consumer.start(); consumer.join(120)
        assert consumer.exitcode == 0, consumer.exitcode
        report['verification'] = json.loads((args.out/'reader.json').read_text())
        call(endpoint, 'retire', key='checkpoint/block0')
        report['after_retirement'] = call(endpoint, 'inventory')[0]
        assert report['after_retirement']['used_bytes'] == 0
        report['status'] = 'passed'
    except BaseException as exc:
        report.update(status='failed', error=repr(exc))
        raise
    finally:
        for process in children:
            if process.is_alive(): process.kill(); process.join(10)
        owner.terminate(); owner.join(10)
        endpoint.unlink(missing_ok=True)
        (args.out/'result.json').write_text(json.dumps(report, indent=2)+'\n')
        print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
