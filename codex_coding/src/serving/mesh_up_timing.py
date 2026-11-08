import sys,time; sys.path.insert(0,"/home/zhujianian/zhujianian/fluidckpt_zjn/main_lib")
from fluidckpt.serving import ctl
t0=time.time(); M=ctl.start(); print("http up",round(time.time()-t0,1),"s", len(M))
t0=time.time(); r=ctl.all_call(M,"mesh"); print("mesh",round(time.time()-t0,1),"s", r[M[0]])
