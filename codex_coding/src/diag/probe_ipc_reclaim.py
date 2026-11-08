"""Measure HIP IPC ownership after repeated imports in isolated small processes."""
import argparse
import gc
import json
import multiprocessing
from pathlib import Path
import resource
import struct


def counter(handle):
    path = Path('/dev/shm') / handle[4].decode().lstrip('/')
    if not path.exists():
        return None
    with path.open('rb') as stream:
        stream.seek(handle[5] * 8)
        return struct.unpack('q', stream.read(8))[0]


def receiver(pipe):
    import torch
    torch.cuda.set_device(0)
    for _ in range(3):
        handle = pipe.recv()
        storage = torch.UntypedStorage._new_shared_cuda(*handle)
        tensor = torch.empty(0, dtype=torch.uint8, device='cuda').set_(storage)
        assert tensor[0].item() == 37 and tensor[-1].item() == 37
        torch.cuda.synchronize()
        del tensor, storage
        gc.collect()
        torch.cuda.ipc_collect()
        torch.cuda.empty_cache()
        pipe.send({'released': True, 'counter_after_import_release': counter(handle)})
    pipe.close()


def producer(mode, output):
    import torch
    torch.cuda.set_device(0)
    torch.set_num_threads(1)
    context = multiprocessing.get_context('spawn')
    parent, child = context.Pipe()
    process = context.Process(target=receiver, args=(child,))
    process.start()
    before = torch.cuda.memory_allocated()
    tensor = torch.full((64 << 20,), 37, dtype=torch.uint8, device='cuda')
    torch.cuda.synchronize()
    handle = None
    rows = []
    try:
        for _ in range(3):
            if handle is None or mode == 'fresh':
                handle = tensor.untyped_storage()._share_cuda_()
            parent.send(handle)
            if not parent.poll(60):
                raise TimeoutError('IPC receiver did not acknowledge release')
            rows.append(parent.recv())
        process.join(30)
        if process.exitcode != 0:
            raise RuntimeError(f'IPC receiver exit: {process.exitcode}')
        del tensor
        gc.collect()
        torch.cuda.ipc_collect()
        torch.cuda.empty_cache()
        torch.cuda.synchronize()
        result = {'mode': mode, 'imports': rows, 'allocated_before': before,
                  'allocated_after_owner_release': torch.cuda.memory_allocated(),
                  'counter_after_owner_release': counter(handle)}
        Path(output).write_text(json.dumps(result, indent=2) + '\n')
    finally:
        if process.is_alive():
            process.kill()
            process.join(10)
        parent.close()


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    context = multiprocessing.get_context('spawn')
    for mode in ['reused', 'fresh']:
        output = args.out / (mode + '.json')
        process = context.Process(target=producer, args=(mode, str(output)))
        process.start()
        process.join(240)
        if process.is_alive():
            process.kill()
            process.join(10)
            raise TimeoutError('IPC producer probe timed out')
        if process.exitcode != 0:
            raise RuntimeError(f'IPC producer exit: {process.exitcode}')
        print(output.read_text(), flush=True)


if __name__ == '__main__':
    main()
