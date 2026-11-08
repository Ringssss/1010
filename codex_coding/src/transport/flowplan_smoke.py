import sys; sys.path.insert(0,"/home/zhujianian/zhujianian/fluidckpt_zjn/main_lib")
from fluidckpt.transport.flowplan import Demand, Copy, solve
S=44*2**30
def scen(n28=True, ssd=True, n27=False):
    ds=[]
    for g in range(8):
        cs=[]
        if n28: cs.append(Copy("dram","node28",gpu=2+g%6))
        if ssd: cs.append(Copy("ssd","node29",path=f"/x/rank{8+g}.bin"))
        if n27: cs.append(Copy("dram","node27",gpu=g))
        ds.append(Demand(8+g,"node29",g,S,cs))
    return ds
for name,kw in [("ssd only",dict(n28=False)),("node28 dram only",dict(ssd=False)),("node28+ssd",{}),("node28+node27+ssd",dict(n27=True))]:
    T,split,load=solve(scen(**kw)); hot=sorted(load.items(),key=lambda x:-x[1])[:3]
    print(f"{name:20s} T={T:7.1f}s  bottlenecks={[(k,round(v,1)) for k,v in hot]}")
