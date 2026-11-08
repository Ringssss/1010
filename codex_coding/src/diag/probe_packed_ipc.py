"""Check packed-offset HIP IPC across processes before a full-model baseline."""
import argparse
import multiprocessing as mp
import pickle
from pathlib import Path
import resource
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT/'main_lib'))


def consume(path, gpu):
    import torch
    from fluidckpt.serving.ipc_attach import attach
    torch.cuda.set_device(gpu)
    model = torch.nn.Module()
    model.weight = torch.nn.Parameter(torch.empty(8, 8, device='meta'))
    model.bias = torch.nn.Parameter(torch.empty(8, device='meta'))
    attach(model, path, gpu)
    assert torch.equal(model.weight.cpu(), torch.arange(64).reshape(8, 8).float())
    assert torch.equal(model.bias.cpu(), torch.arange(8).float()+100)
    import gc
    del model
    gc.collect(); torch.cuda.synchronize(); torch.cuda.ipc_collect()


if __name__ == '__main__':
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(); parser.add_argument('--gpu', type=int, default=6)
    parser.add_argument('--out', type=Path, required=True); args = parser.parse_args()
    import torch
    torch.cuda.set_device(args.gpu)
    packed = torch.zeros(4096, dtype=torch.uint8, device=f'cuda:{args.gpu}')
    packed[1024:1280].view(torch.float32).copy_(torch.arange(64, device=packed.device).float())
    packed[2048:2080].view(torch.float32).copy_(torch.arange(8, device=packed.device).float()+100)
    torch.cuda.synchronize()
    row = {'name': 'weight', 'kind': 'param', 'storage_name': 'weight', 'dtype': 'torch.float32',
           'shape': [8, 8], 'stride': [8, 1], 'storage_offset': 0}
    entry = {'name': 'weight', 'nbytes': 256, 'packed_offset_bytes': 1024,
             'handle': packed.untyped_storage()._share_cuda_()}
    bias = dict(row, name='bias', storage_name='bias', shape=[8], stride=[1])
    bias_entry = dict(entry, name='bias', nbytes=32, packed_offset_bytes=2048)
    with args.out.open('wb') as stream:
        pickle.dump({'meta': {'tensors': [row, bias]}, 'entries': [entry, bias_entry]}, stream)
    child = mp.get_context('spawn').Process(target=consume, args=(str(args.out), args.gpu))
    child.start(); child.join(30)
    if child.is_alive(): child.kill(); child.join(); raise TimeoutError('IPC consumer did not finish')
    if child.exitcode: raise RuntimeError(f'IPC consumer exit={child.exitcode}')
    print('packed HIP IPC offset verified across processes')
