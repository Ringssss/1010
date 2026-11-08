import sys; sys.path.insert(0,"/home/zhujianian/zhujianian/fluidckpt_zjn/main_lib")
from fluidckpt.transport.flowplan import Demand, Copy, solve, to_ops
from fluidckpt.serving.ctl import parse_mesh
M=parse_mesh(); wid=lambda n,g: M.index((n,g))
S=44*2**30
ds=[Demand(8+g,"node29",g,S,[Copy("dram","node28",gpu=2+g%6),Copy("dram","node29",gpu=g)]) for g in range(8)]
T,split,load=solve(ds); print("T",round(T,2))
ops=to_ops(ds,split,wid); 
for o in ops[:6]: print(o)
print(len(ops), sum(o["n"] for o in ops)/2**30)
