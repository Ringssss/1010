"""What does host memory cost on this stack? Pin time, H2D rate by memory kind, and whether a
cuda-pinned buffer can also be an RDMA region. Prints one JSON line per fact."""
import ctypes, json, mmap, sys, threading, time
import torch
from fluidckpt.live import rdma
G = 1 << 30; M = 1 << 20
card = int(sys.argv[1]) if len(sys.argv) > 1 else 0
dev = torch.device("cuda", card)
def say(**k): print(json.dumps(k), flush=True)
def sync(): torch.cuda.synchronize(dev)
t = time.time(); gpu = torch.empty(4 * G, dtype=torch.uint8, device=dev); gpu.zero_(); sync(); say(fact="gpu_alloc_4G", s=round(time.time() - t, 3))
for mib in (256, 1024, 4096):
    t = time.time(); x = torch.empty(mib * M, dtype=torch.uint8, pin_memory=True); dt = time.time() - t
    say(fact="pin_alloc", MiB=mib, s=round(dt, 3), GBps=round(mib * M / dt / 1e9, 2)); del x
t = time.time(); xs = [None] * 8
def one(i): xs[i] = torch.empty(1024 * M, dtype=torch.uint8, pin_memory=True)
th = [threading.Thread(target=one, args=(i,)) for i in range(8)]; [x.start() for x in th]; [x.join() for x in th]
dt = time.time() - t; say(fact="pin_alloc_8x1G_parallel", s=round(dt, 3), GBps=round(8 * G / dt / 1e9, 2)); del xs
pin = torch.empty(4 * G, dtype=torch.uint8, pin_memory=True); pin.fill_(7)
for rep in range(3):
    t = time.time(); gpu.copy_(pin, non_blocking=True); sync(); dt = time.time() - t
    say(fact="h2d_pinned_4G", rep=rep, GBps=round(4 * G / dt / 1e9, 2))
# 64 MiB pieces, as the restore pipeline does
t = time.time()
for off in range(0, 4 * G, 64 * M): gpu[off:off + 64 * M].copy_(pin[off:off + 64 * M], non_blocking=True)
sync(); dt = time.time() - t; say(fact="h2d_pinned_64MiB_pieces", GBps=round(4 * G / dt / 1e9, 2))
for rep in range(2):
    t = time.time(); pin.copy_(gpu, non_blocking=True); sync(); dt = time.time() - t
    say(fact="d2h_pinned_4G", rep=rep, GBps=round(4 * G / dt / 1e9, 2))
mm = mmap.mmap(-1, 2 * G); addr = ctypes.addressof(ctypes.c_char.from_buffer(mm)); ctypes.memset(addr, 3, 2 * G)
page = torch.frombuffer(mm, dtype=torch.uint8)
for rep in range(2):
    t = time.time(); gpu[:2 * G].copy_(page); sync(); dt = time.time() - t
    say(fact="h2d_pageable_2G", rep=rep, GBps=round(2 * G / dt / 1e9, 2))
# hipHostRegister on plain mmap memory
try:
    hip = ctypes.CDLL("libamdhip64.so"); hip.hipHostRegister.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint]
    t = time.time(); rc = hip.hipHostRegister(addr, 2 * G, 0); dt = time.time() - t
    say(fact="hipHostRegister_2G", rc=rc, s=round(dt, 3), GBps=round(2 * G / dt / 1e9, 2))
    for rep in range(2):
        t = time.time(); gpu[:2 * G].copy_(page); sync(); dt = time.time() - t
        say(fact="h2d_hostregistered_2G", rep=rep, GBps=round(2 * G / dt / 1e9, 2))
except Exception as e:
    say(fact="hipHostRegister", error=repr(e))
# cuda-pinned buffer as an RDMA region + loopback READ from the mmap region
try:
    me = sys.argv[2]
    t = time.time(); src = rdma.Region(0, addr, 2 * G, True); say(fact="ibv_reg_hostregistered_2G", s=round(time.time() - t, 3))
    t = time.time(); dst = rdma.Region(0, pin.data_ptr(), 4 * G, False); say(fact="ibv_reg_cuda_pinned_4G", s=round(time.time() - t, 3))
    rdma.serve(me, 18801); c = rdma.Conn(me, me, 18801)
    for rep in range(2):
        t = time.time(); c.read(addr, src.rkey, pin.data_ptr(), dst.lkey, 2 * G); dt = time.time() - t
        say(fact="rdma_loopback_into_cuda_pinned_2G", rep=rep, GBps=round(2 * G / dt / 1e9, 2), ok=bool((pin[:2 * G:4097] == 3).all()))
    t = time.time(); c.read(addr, src.rkey, pin.data_ptr(), dst.lkey, 2 * G); gpu[:2 * G].copy_(pin[:2 * G], non_blocking=True); sync()
    say(fact="rdma_then_h2d_2G", GBps=round(2 * G / (time.time() - t) / 1e9, 2), ok=bool((gpu[:2 * G:4097] == 3).all()))
except Exception as e:
    say(fact="rdma_on_pinned", error=repr(e))
