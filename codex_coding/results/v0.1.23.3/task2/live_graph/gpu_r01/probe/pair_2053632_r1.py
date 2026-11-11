
import json, time, torch, torch.distributed as dist, os
def st(**kw):
    kw['t'] = round(time.time() % 10000, 3); print(json.dumps(kw), flush=True)
torch.cuda.set_device(0)
dist.init_process_group('nccl', init_method='tcp://12.11.3.27:39620', rank=1, world_size=2,
                        timeout=__import__('datetime').timedelta(seconds=120))
st(ev='world', rank=1)
g = dist.new_group([0, 1])
st(ev='group', rank=1)
x = torch.zeros(1, device='cuda')
dist.broadcast(x, src=0, group=g)
st(ev='warm', rank=1)
n = int(1073741824)
c = int(256 << 20)
buf = torch.empty(c, dtype=torch.uint8, device='cuda')
side = torch.cuda.Stream() if True == "True" else None
if 1 == 0:
    buf.fill_(7)
torch.cuda.synchronize()
dist.barrier()
t0 = time.time()
sent = 0
while sent < n:
    m = min(c, n - sent)
    if side is None:
        dist.broadcast(buf[:m], src=0, group=g)
    else:
        with torch.cuda.stream(side):
            dist.broadcast(buf[:m], src=0, group=g)
    sent += m
if side is None:
    torch.cuda.synchronize()
else:
    side.synchronize()
dt = time.time() - t0
st(ev='stream', rank=1, GiB=round(n/2**30, 3), s=round(dt, 4), GBps=round(n/dt/1e9, 2))
