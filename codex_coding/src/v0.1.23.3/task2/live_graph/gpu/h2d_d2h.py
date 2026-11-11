"""Read/write asymmetry: isolate H2D and D2H at model-scale sizes with pinned staging."""
import json, sys, time
import torch

n = int(float(sys.argv[1] if len(sys.argv) > 1 else 8) * 2**30)
torch.cuda.set_device(0)
dev = torch.device("cuda", 0)
pinned = torch.empty(int(1 << 30), dtype=torch.uint8, pin_memory=True)
pinned.fill_(3)
gpu = torch.empty(int(1 << 30), dtype=torch.uint8, device=dev)
gpu.fill_(5)
big_h = torch.empty(n, dtype=torch.uint8, pin_memory=True)
big_d = torch.empty(n, dtype=torch.uint8, device=dev)
torch.cuda.synchronize()

res = {"GiB": n / 2**30}
# whole-buffer, one copy each way
torch.cuda.synchronize(); t0 = time.perf_counter(); big_d.copy_(big_h, non_blocking=False); torch.cuda.synchronize()
res["h2d_whole_GBps"] = round(n / (time.perf_counter() - t0) / 1e9, 2)
torch.cuda.synchronize(); t0 = time.perf_counter(); big_h.copy_(big_d, non_blocking=False); torch.cuda.synchronize()
res["d2h_whole_GBps"] = round(n / (time.perf_counter() - t0) / 1e9, 2)

# chunked through a 1 GiB staging buffer: the shape a restore actually uses
chunk = 1 << 30
torch.cuda.synchronize(); t0 = time.perf_counter()
for off in range(0, n, chunk):
    m = min(chunk, n - off)
    gpu[:m].copy_(big_h[off:off + m], non_blocking=True)
torch.cuda.synchronize()
res["h2d_chunked_GBps"] = round(n / (time.perf_counter() - t0) / 1e9, 2)

torch.cuda.synchronize(); t0 = time.perf_counter()
for off in range(0, n, chunk):
    m = min(chunk, n - off)
    pinned[:m].copy_(big_d[off:off + m], non_blocking=True)
torch.cuda.synchronize()
res["d2h_chunked_GBps"] = round(n / (time.perf_counter() - t0) / 1e9, 2)
print(json.dumps(res))
