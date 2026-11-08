"""Cross-process HIP IPC probe: export N tensors in a producer, map them zero-copy in a
separate process, verify the bytes match, and time the handshake.

The baseline (SGLang's Weight Cache Daemon) is exactly this: producer holds quantized TP-sharded
weights in HBM, exports `storage._share_cuda_()` handles, a consumer maps them and skips loading.
Two numbers matter for us and are measured here:

  * per-handle map cost  -> how many handles we can afford (one per tensor vs one per layer)
  * correctness         -> a mapped tensor must be bit-identical, not just the right shape

  python scripts/ipc_probe.py --tensors 64 --chunk-MiB 16
"""
import argparse
import multiprocessing as mp
import time

import torch


def producer(q, ntensors, chunk_bytes):
    torch.cuda.set_device(0)
    tensors = [torch.randn(chunk_bytes // 2, dtype=torch.float16, device="cuda")
               for _ in range(ntensors)]
    torch.cuda.synchronize()
    handles = []
    t0 = time.perf_counter()
    for t in tensors:
        h = t.untyped_storage()._share_cuda_()
        handles.append((h, list(t.shape), str(t.dtype)))
    dt = time.perf_counter() - t0
    total = sum(t.numel() * t.element_size() for t in tensors)
    print(f"[producer] exported {ntensors} handles, {total/2**30:.2f} GiB, "
          f"{dt*1e3:.2f} ms total ({dt/ntensors*1e6:.1f} us/handle)", flush=True)
    q.put((handles, [t.clone() for t in tensors[:2]]))  # send 2 tensors for verification
    q.get()  # hold the originals alive until the child is done


def consumer(q):
    torch.cuda.set_device(0)
    handles, ref = q.get()
    t0 = time.perf_counter()
    mapped = []
    for h, shape, dtype in handles:
        st = torch.UntypedStorage._new_shared_cuda(*h)
        mapped.append(torch.empty(0, dtype=getattr(torch, dtype.split(".")[-1]),
                                  device="cuda").set_(st).view(shape))
    dt = time.perf_counter() - t0
    total = sum(m.numel() * m.element_size() for m in mapped)
    print(f"[consumer] mapped {len(mapped)} storages, {total/2**30:.2f} GiB, "
          f"{dt*1e3:.2f} ms total ({dt/len(mapped)*1e6:.1f} us/handle)", flush=True)
    ok = all(torch.equal(m, r) for m, r in zip(mapped[:2], ref))
    print(f"[consumer] bitwise identical: {ok}", flush=True)
    print(f"[consumer] peak HBM this process: {torch.cuda.max_memory_allocated()/2**30:.2f} GiB "
          f"(should be ~0 if truly zero-copy)", flush=True)
    q.put(ok)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tensors", type=int, default=64)
    ap.add_argument("--chunk-MiB", type=int, default=16)
    a = ap.parse_args()
    q = mp.Queue()
    p = mp.Process(target=producer, args=(q, a.tensors, a.chunk_MiB * 2**20))
    p.start()
    ok = consumer(q)
    if ok:
        q.get()
    p.join()
