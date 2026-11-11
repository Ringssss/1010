"""From scattered chunks to a serving process, end to end.

1. the pool parks a model as chunks on other nodes only (nothing on the serving node);
2. it is restored into HBM on the serving node by the node daemon;
3. THIS process (a separate "engine") maps that HBM through a HIP IPC handle -- zero copy --
   builds every tensor as a view at its safetensors offset, binds them into a transformers model
   skeleton and generates tokens;
4. the output is compared with the same model loaded the ordinary way from the original files.

Run inside the serving node's container: servable_demo.py <manifest_dir> <model> <card> <out_dir>"""
import base64, json, os, sys, time
import torch
from fluidckpt.live.pool import GiB, Pool

NODES = {"node27": "12.11.3.27", "node28": "12.11.3.28", "node29": "12.11.3.29", "node30": "12.11.3.30"}
HOME = "node28"
man, name, card, out = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
os.makedirs(out, exist_ok=True)
logf = open(os.path.join(out, "servable_demo.log"), "a")
def say(**k):
    line = json.dumps(k, ensure_ascii=False); logf.write(line + "\n"); logf.flush(); print(line[:400], flush=True)
DT = {"F16": torch.float16, "BF16": torch.bfloat16, "F32": torch.float32, "F64": torch.float64,
      "I64": torch.int64, "I32": torch.int32, "I16": torch.int16, "I8": torch.int8, "U8": torch.uint8, "BOOL": torch.bool}
dev = f"cuda:{card}"
torch.zeros(1, device=dev)                                   # engine process already up, context warm

pool = Pool(NODES, log=lambda **k: None); pool.start(react=False)
m = pool.register(name, f"{man}/{name}.json", HOME)
p = pool.park(name, copies=2, tiers=("dram",), avoid_nodes=(HOME,))
say(event="parked", model=name, GiB=p["GiB"], fragments={v: g for v, g in p["vertices"].items()})

t0 = time.time()
r = pool.restore(name, HOME, [card], allow=("dram",), release=False)
t_restore = time.time() - t0
h = pool.rpc[HOME].call("job_export", job=r["job"])[str(card)]
st = torch.UntypedStorage._new_shared_cuda(*tuple(base64.b64decode(x) if isinstance(x, str) else x for x in h))
buf = torch.empty(0, dtype=torch.uint8, device=dev).set_(st)
base, _ = pool.layout(m, [card])
state, copied = {}, 0
for tname, t in m.manifest.tensors.items():
    off = base[t["file"]][1] + t["offset"]
    dt = DT[t["dtype"]]
    raw = buf[off:off + t["nbytes"]]
    if off % dt.itemsize:                                    # misaligned in the file: one small copy
        raw = raw.clone(); copied += 1
    state[tname] = raw.view(dt).reshape(t["shape"])
t_attach = time.time() - t0 - t_restore
from transformers import AutoConfig, AutoModelForCausalLM, AutoTokenizer
cfg = AutoConfig.from_pretrained(m.dir)
with torch.device(dev):
    model = AutoModelForCausalLM.from_config(cfg, torch_dtype=next(iter(state.values())).dtype)
missing, unexpected = model.load_state_dict(state, strict=False, assign=True)
if hasattr(model, "tie_weights"): model.tie_weights()
model.eval()
t_ready = time.time() - t0
tok = AutoTokenizer.from_pretrained(m.dir)
ids = tok("The capital of France is", return_tensors="pt").to(dev)
with torch.no_grad():
    t1 = time.time(); o = model.generate(**ids, max_new_tokens=12, do_sample=False); t_gen = time.time() - t1
    logits = model(**ids).logits[0, -1].float()
text = tok.decode(o[0], skip_special_tokens=True)
shared = sum(1 for v in model.state_dict().values() if v.device.type == "cuda") 
say(event="served_from_pool", restore_to_hbm_s=round(t_restore, 3), ipc_attach_and_views_s=round(t_attach, 3),
    request_to_ready_s=round(t_ready, 3), first_generate_s=round(t_gen, 3), tensors=len(state), copied_misaligned=copied,
    missing=list(missing)[:5], unexpected=list(unexpected)[:5], text=text,
    paths={k: round(v["bytes"] / GiB, 2) for k, v in r["paths"].items()})
# reference: the ordinary loader on the original files
t2 = time.time()
ref = AutoModelForCausalLM.from_pretrained(m.dir, torch_dtype=model.dtype).to(dev).eval()
t_ref = time.time() - t2
with torch.no_grad():
    o2 = ref.generate(**ids, max_new_tokens=12, do_sample=False)
    l2 = ref(**ids).logits[0, -1].float()
say(event="reference_loader", from_pretrained_s=round(t_ref, 3), same_tokens=bool(torch.equal(o, o2)),
    max_logit_diff=float((logits - l2).abs().max()), text=tok.decode(o2[0], skip_special_tokens=True))
del model, state, buf, st
pool.rpc[HOME].call("job_release", job=r["job"]); pool.unpark(name); pool.stop()
