"""Attach weights that FluidPool's node daemon restored into HBM to a running SGLang engine.

Activation path (identical for every system compared; only how the bytes reached HBM differs):

  1. The engine is started once with ``--load-format dummy`` and
     ``--custom-weight-loader fluidckpt.serve.sglang_attach.load_from_noded``. Its runtime
     (HIP context, CUDA graphs, KV pool) is warm; its parameters hold random values.
  2. A restore lands the model's *raw safetensors bytes* on the engine's cards. These are noded
     job buffers, laid out by ``Pool.layout``: file f sits at byte ``base[f]`` of the job buffer of
     card ``card[f]``.
  3. ``describe_job`` writes a small JSON descriptor: per-card HIP IPC handles of the job buffers
     (``noded.op_job_export``), the file layout and the manifest's tensor table.
  4. ``Engine.update_weights_from_tensor([("fluidpool:<descriptor>", torch.zeros(1))],
     load_format=LOADER)`` makes every TP worker call ``load_from_noded(model, named_tensors)``.
     That function imports the IPC buffers, builds one zero-copy view per checkpoint tensor and
     feeds them to ``model.load_weights`` -- SGLang's own name mapping, QKV/gate-up fusion and TP
     sharding. The model's parameters receive a device-to-device copy (same card, or over xGMI when
     a tensor's file sits on another card of the node). After that the job buffers can be released.

Why this mechanism (and not SGLang Weight Cache's zero-copy IPC loader): the installed SGLang
(0.5.12, dtk2604) has no ``srt/weight_cache`` package; and zero-copy aliasing needs the
*post-processed* parameter layout (fused qkv, TP shard, quant transforms), while FluidPool keeps
the checkpoint's raw bytes so that one copy serves every TP layout. ``load_weights`` does that
transform at HBM speed; the cost is reported separately as attach time.

Scope: unquantized checkpoints (BF16/F16/F32). Quantized models need
``process_weights_after_loading`` after the copy (see ``post_process`` below; untested).
"""
from __future__ import annotations

import base64
import json
import os
import time

DTYPES = {
    "BF16": "bfloat16", "F16": "float16", "F32": "float32", "F64": "float64",
    "I64": "int64", "I32": "int32", "I16": "int16", "I8": "int8", "U8": "uint8", "BOOL": "bool",
    "F8_E4M3": "float8_e4m3fn", "F8_E5M2": "float8_e5m2",
}
PREFIX = "fluidpool:"
LOADER = "fluidckpt.serve.sglang_attach.load_from_noded"
DESC_DIR = "/dev/shm/fluidpool_attach"


# ---------------------------------------------------------------- coordinator side
def describe_job(pool, name, node, cards, job, path=None, log_path=None):
    """Write the attach descriptor of restored job `job` (model `name` on `node`'s `cards`).

    Runs in the coordinator (any process that holds the Pool). Returns the descriptor path. The
    engine must run on `node` (HIP IPC is node-local) and see the cards under their physical
    indices (start it with base_gpu_id, not with a narrowed HIP_VISIBLE_DEVICES), or pass
    `device_of` in the descriptor (see load_from_noded)."""
    model = pool.models[name]
    base, _ = pool.layout(model, list(cards))
    exports = pool.rpc[node].call("job_export", job=job)
    tensors = [[t, v["file"], v["offset"], v["nbytes"], v["dtype"], v["shape"]]
               for t, v in model.manifest.tensors.items() if v["file"] in base]
    desc = {"model": name, "node": node, "job": job, "cards": [int(c) for c in cards],
            "layout": {f: [int(c), int(o)] for f, (c, o) in base.items()},
            "exports": exports, "tensors": tensors, "log": log_path, "t": time.time()}
    os.makedirs(DESC_DIR, exist_ok=True)
    path = path or os.path.join(DESC_DIR, f"{job}.json")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(desc, f)
    os.replace(tmp, path)
    return path


def request(desc_path):
    """The named_tensors argument for Engine.update_weights_from_tensor."""
    import torch
    return [(PREFIX + desc_path, torch.zeros(1))]


# ---------------------------------------------------------------- engine side
def _decode_handle(h):
    """op_job_export base64-encodes the bytes fields of storage._share_cuda_()."""
    return tuple(base64.b64decode(x) if isinstance(x, str) else x for x in h)


def import_buffers(desc, device_of=None):
    """{card: uint8 tensor over the job buffer of that card}, mapped through HIP IPC.

    device_of: physical card -> local device ordinal of this process (default: identity, or
    the descriptor's "device_of"). The handle's device field is the daemon's ordinal, which is
    the physical card because noded sees every card."""
    import torch
    device_of = device_of or {int(k): v for k, v in (desc.get("device_of") or {}).items()}
    out, keep = {}, []
    for card_s, e in desc["exports"].items():
        card = int(card_s)
        dev = device_of.get(card, card)
        h = list(_decode_handle(e["handle"]))
        h[0] = dev
        st = torch.UntypedStorage._new_shared_cuda(*h)
        keep.append(st)
        t = torch.empty(0, dtype=torch.uint8, device=f"cuda:{dev}").set_(st)
        out[card] = t[e["offset"]:e["offset"] + e["nbytes"]]
    return out, keep


def iter_tensors(desc, bufs):
    """(checkpoint tensor name, zero-copy view with its dtype and shape), in manifest order."""
    import torch
    for name, file, off, nbytes, dtype, shape in desc["tensors"]:
        card, base = desc["layout"][file]
        raw = bufs[card][base + off:base + off + nbytes]
        t = raw.view(getattr(torch, DTYPES[dtype]))
        yield name, t.view(shape) if shape else t.view(())


def load_from_noded(model, named_tensors):
    """SGLang custom weight loader (server_args.custom_weight_loader). Called in every TP worker
    by ModelRunner.update_weights_from_tensor with the (name, tensor) pairs of the request; the
    first name carries the descriptor path."""
    import torch
    name = named_tensors[0][0]
    if not name.startswith(PREFIX):
        raise ValueError(f"load_from_noded: expected a '{PREFIX}<path>' entry, got {name!r}")
    desc = json.load(open(name[len(PREFIX):]))
    t0 = time.perf_counter()
    bufs, keep = import_buffers(desc)
    t1 = time.perf_counter()
    model.load_weights(iter_tensors(desc, bufs))
    torch.cuda.synchronize()
    t2 = time.perf_counter()
    post_process(model)
    del bufs, keep                                       # copy semantics: the job can be released
    torch.cuda.synchronize()
    rec = {"event": "attach", "model": desc["model"], "job": desc["job"], "pid": os.getpid(),
           "device": torch.cuda.current_device(), "ipc_import_s": round(t1 - t0, 4),
           "load_weights_s": round(t2 - t1, 4), "total_s": round(time.perf_counter() - t0, 4),
           "tensors": len(desc["tensors"]), "t": time.time()}
    if desc.get("log"):
        with open(desc["log"], "a") as f:
            f.write(json.dumps(rec) + "\n")
    return rec


def post_process(model):
    """Quantized models: re-run the quant methods' process_weights_after_loading on the freshly
    loaded raw weights (DefaultModelLoader does this after load_weights). A no-op for the
    unquantized models this path is validated on; untested for quantized ones."""
    for module in model.modules():
        qm = getattr(module, "quant_method", None)
        if qm is None or type(qm).__name__ in ("UnquantizedLinearMethod", "UnquantizedEmbeddingMethod",
                                               "UnquantizedFusedMoEMethod"):
            continue
        fn = getattr(qm, "process_weights_after_loading", None)
        if fn is not None:
            fn(module)
