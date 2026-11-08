"""Hook FluidCKPT into SGLang's model loading without touching SGLang's source.

SGLang starts its TP workers with multiprocessing "spawn", which re-imports the launcher's main
module in every child. scripts/sglang_fluid_launch.py imports this module at top level, so
`install()` runs inside every TP worker before the model loads.

Two modes, chosen by FLUIDCKPT_MODE:

  capture  - normal load (safetensors from disk). After load + process_weights_after_loading the
             worker exports one HIP IPC handle per unique parameter/buffer storage into
             $FLUIDCKPT_HANDOFF/<tag>/rank<tp>.pkl. The local keeper maps them and copies the
             post-processed shard into its own memory. That snapshot is what gets kept alive.

  restore  - run with --load-format dummy. The worker builds the model skeleton and runs
             process_weights_after_loading (same as SGLang's DummyModelLoader, so storages have
             the post-processed layout), skips random init, exports the (empty) storages the
             same way, and blocks until the keeper writes rank<tp>.done after filling them.
             Then _post_load_weights and SGLang continues as if it had read the checkpoint.

Storages are matched by (name of the first tensor on the storage, nbytes), so a snapshot taken
from one process restores into another as long as both built the same model on the same TP rank.

Derived MLA tensors (plain attributes, not params/buffers)
----------------------------------------------------------
`post_load_weights` fuses `kv_b_proj` into `self_attn.w_kc` / `w_vc` (and sets `w_scale`) and
assigns them as *plain Python attributes* on the attention module. They are invisible to both
`named_parameters()` and `named_buffers()`, yet the forward pass reads them directly as bmm
operands. On this image (gfx936, `_is_hcu=True`) they come out bf16, laid out as
`w_kc[heads, qk_nope, kv_lora]` and `w_vc[heads, kv_lora, v_head]`.

They cannot be re-derived on the restore path, because doing so requires `post_load_weights`,
which the dummy loader calls *after* `process_weights_after_loading` -- the opposite of the real
loader (`load_weights` -> `post_load_weights` -> `process_weights_after_loading`). By then the
block-fp8 method has already transposed `kv_b_proj.weight` to `[kv_lora, heads*448]`, so
`w.unflatten(0, (-1, 448))` raises:

    RuntimeError: unflatten: Provided sizes [-1, 448] don't multiply up to the size of dim 0
    (512) in the input tensor

So the capture records these attributes explicitly (they are ~137 MB/rank, free next to 45 GiB
of params) and the restore installs them, skipping `_post_load_weights` entirely.
"""
from __future__ import annotations

import json
import logging
import os
import pickle
import time

logger = logging.getLogger("fluidckpt.sglang_hook")


def _handoff_dir():
    d = os.path.join(os.environ.get("FLUIDCKPT_HANDOFF", "/dev/shm/fluidckpt"),
                     os.environ.get("FLUIDCKPT_TAG", "default"))
    os.makedirs(d, exist_ok=True)
    return d


def _derived_attrs(model, prefix):
    """Yield (name, tensor) for every tensor held as a plain attribute rather than a param/buffer.

    Only tensors are yielded; `w_scale` is a bare float on the HCU path (the fusion folds it into
    w_kc/w_vc and resets it to 1.0), so it needs no storage of its own. A snapshot that recorded
    it as a tensor *would* need one, so it is carried in the sidecar instead.

    This walks the module tree generically instead of checking a fixed name list. The list version
    is how the GDN layers were missed: `linear_attn.attn` pre-converts its conv1d weight into a
    bare `conv_weights` attribute at construction time, which is a name no MLA-oriented list would
    ever carry. Because the restore path builds its skeleton under `torch.device("meta")`, a missed
    attribute stays a meta tensor with `data_ptr() == 0`, and the first kernel that reads it --
    `causal_conv1d_fn` -- faults at a near-null address. Enumerating what actually exists removes
    the whole class of bug rather than one instance of it.
    """
    import torch
    for mod_name, mod in model.named_modules():
        if not mod_name.startswith(prefix):
            continue
        buffers = getattr(mod, "_buffers", {})
        params = getattr(mod, "_parameters", {})
        for attr, t in vars(mod).items():
            # Params and buffers are already covered by their own walks; skipping them here keeps
            # one tensor from being exported twice under two names.
            if attr in buffers or attr in params or not torch.is_tensor(t):
                continue
            if t.device.type != "cuda" or t.numel() == 0:
                continue
            # A module may hold the same tensor under several attributes; storage-level dedup in
            # the caller already collapses those, so only the first name is reported here.
            yield f"{mod_name}.{attr}", t


def export_storages(model, tp_rank: int, gpu_id: int, phase: str):
    """One IPC handle per unique storage: named_parameters, then named_buffers, then the MLA
    derived attributes that neither of those two walks can see (`_derived_attrs`)."""
    import torch
    from fluidckpt.serving.layout import scalar_attributes
    seen = {}
    entries = []
    tensors = []
    walks = [("param", model.named_parameters(remove_duplicate=False)),
             ("buffer", model.named_buffers(remove_duplicate=False)),
             ("attr", _derived_attrs(model, "model."))]
    for kind, it in walks:
        for name, t in it:
            if t is None or t.device.type != "cuda" or t.numel() == 0:
                continue
            st = t.untyped_storage()
            key = st.data_ptr()
            tensors.append({"name": name, "kind": kind, "dtype": str(t.dtype),
                            "shape": list(t.shape), "stride": list(t.stride()),
                            "storage_offset": t.storage_offset(),
                            "attributes": scalar_attributes(t),
                            "storage_name": seen.get(key, name)})
            if key in seen:
                continue
            seen[key] = name
            entries.append({"name": name, "kind": kind, "nbytes": st.nbytes(),
                            "dtype": str(t.dtype), "shape": list(t.shape),
                            "handle": st._share_cuda_()})
    torch.cuda.synchronize()
    d = _handoff_dir()
    if phase == "capture":
        layout = {"storages": [{k: v for k, v in e.items() if k != "handle"} for e in entries],
                  "tensors": tensors}
        layout_path = os.path.join(d, f"layout.rank{tp_rank}.json")
        with open(layout_path + ".tmp", "w") as f:
            json.dump(layout, f)
        os.replace(layout_path + ".tmp", layout_path)
        # Sidecar for the restore side, which starts from a skeleton whose derived attrs are all
        # None and therefore cannot discover their shape/dtype on its own.
        spec = {name: {"shape": list(t.shape), "dtype": str(t.dtype)}
                for name, t in _derived_attrs(model, "model.")}
        tmpj = os.path.join(d, f".derived.rank{tp_rank}.json.tmp")
        with open(tmpj, "w") as f:
            json.dump(spec, f)
        rank_manifest = os.path.join(d, f"derived.rank{tp_rank}.json")
        os.replace(tmpj, rank_manifest)
        with open(tmpj, "w") as f:
            json.dump(spec, f)
        os.replace(tmpj, os.path.join(d, "derived.json"))
        if spec:
            logger.warning("[fluidckpt] rank %d derived manifest: %d tensors", tp_rank, len(spec))
    meta = {"tp_rank": tp_rank, "gpu_id": gpu_id, "pid": os.getpid(), "phase": phase,
            "model_identity": os.environ.get("FLUIDCKPT_MODEL_ID"),
            "tp_size": os.environ.get("FLUIDCKPT_TP_SIZE"), "tensors": tensors,
            "host": os.uname().nodename, "t_export": time.time(),
            "n": len(entries), "bytes": sum(e["nbytes"] for e in entries)}
    tmp = os.path.join(d, f".rank{tp_rank}.pkl.tmp")
    with open(tmp, "wb") as f:
        pickle.dump({"meta": meta, "entries": entries}, f)
    if phase == "restore":
        from fluidckpt.serving.measure import emit
        # Record before making the file visible, so a fast keeper cannot precede this event.
        emit(tp_rank, "target_exported")
    os.replace(tmp, os.path.join(d, f"rank{tp_rank}.pkl"))
    logger.warning("[fluidckpt] rank %d exported %d storages, %.2f GiB (%s)",
                   tp_rank, len(entries), meta["bytes"] / 2**30, phase)
    return meta


def _install_derived(model, tp_rank: int, gpu_id: int):
    """Allocate the MLA fused weights on a restoring skeleton so export_storages can publish them.

    Reads the manifest the capture wrote for this tp rank, looking first in the local handoff dir
    and then in $FLUIDCKPT_MANIFEST (where a cross-node launcher stages it, since the capture ran
    on a different node). Every tensor is allocated at its captured shape and dtype, which is what
    the forward pass indexes into (`w_kc[heads, qk_nope, kv_lora]` and `w_vc[heads, kv_lora,
    v_head]`, both bf16 on the HCU path); the bytes themselves arrive from the keeper like any
    other parameter. `w_scale` stays at its init value of 1.0, which is exactly what the fusion
    leaves behind on this path, so it needs no transfer.

    Shape depends only on tp_size (local heads = kv proj width / tp / (qk_nope + v_head)), so the
    manifest is rank-independent and a single staged copy serves every rank.
    """
    import torch
    cands = [os.path.join(_handoff_dir(), f"derived.rank{tp_rank}.json"),
             os.path.join(_handoff_dir(), "derived.json")]
    env = os.environ.get("FLUIDCKPT_MANIFEST")
    if env:
        cands.insert(0, os.path.join(os.path.dirname(env), f"derived.rank{tp_rank}.json"))
        cands.append(env)
    path = next((p for p in cands if os.path.exists(p)), None)
    if path is None:
        raise RuntimeError(
            "no derived manifest: looked in " + ", ".join(cands) + ". The capture writes one into "
            "its handoff dir; for a cross-node restore stage it (e.g. save it to SSD with the "
            "snapshot and set FLUIDCKPT_MANIFEST to its path).")
    with open(path) as f:
        spec = json.load(f)
    mods = dict(model.named_modules())
    for name, d in spec.items():
        mod_name, attr = name.rsplit(".", 1)
        mod = mods.get(mod_name)
        if mod is None:
            raise RuntimeError(f"restore skeleton has no module {mod_name!r} for {name!r}")
        dtype = getattr(torch, d["dtype"].split(".")[-1])
        setattr(mod, attr, torch.empty(*d["shape"], dtype=dtype, device=f"cuda:{gpu_id}"))
    logger.warning("[fluidckpt] rank %d installed %d derived MLA tensors from %s",
                   tp_rank, len(spec), path)
    return len(spec)


def _wait_done(tp_rank: int, timeout: float, suffix: str = "done"):
    path = os.path.join(_handoff_dir(), f"rank{tp_rank}.{suffix}")
    t0 = time.time()
    while not os.path.exists(path):
        if time.time() - t0 > timeout:
            raise TimeoutError(f"keeper did not fill rank {tp_rank} within {timeout}s")
        time.sleep(0.005)
    with open(path) as f:
        return json.load(f)


def install():
    mode = os.environ.get("FLUIDCKPT_MODE", "")
    if mode not in ("capture", "restore", "attach"):
        return
    import torch
    from sglang.srt.model_loader import loader as L
    if not hasattr(L.DefaultModelLoader, '_fluidckpt_original_load_model'):
        L.DefaultModelLoader._fluidckpt_original_load_model = L.DefaultModelLoader.load_model

    if mode == "attach":
        # Zero-copy warm start off a LIVE producer's HBM. SGLang's own fast path: the producer
        # exported one HIP handle per post-processed storage, and this process builds a meta
        # skeleton (no storage of its own) and binds those handles in. Nothing is copied and no
        # checkpoint is read, so the interval being measured is skeleton construction alone.
        def load_model(self, *, model_config, device_config):
            from fluidckpt.serving import ipc_attach
            from fluidckpt.serving.measure import emit
            tp = self.load_config.tp_rank
            emit(tp, "load_begin_attach")
            t0 = time.time()
            quant_config = L._get_quantization_config(model_config, self.load_config)
            # No device context: modules are built on the meta device so the skeleton costs no HBM.
            # A real skeleton plus a live producer does not fit -- the model is 34 GiB per rank and
            # the card is 64 GiB.
            with L.set_default_torch_dtype(model_config.dtype), torch.device("meta"):
                model = L._initialize_model(model_config, self.load_config, quant_config)
            t1 = time.time()
            # The producer's storages are already post-processed; the block-fp8 transform would
            # transpose and rescale weights that are already in served layout.
            pkl = os.environ["FLUIDCKPT_ATTACH_PKL"]
            if os.path.isdir(pkl):
                pkl = os.path.join(pkl, f"rank{tp}.pkl")
            info = ipc_attach.attach(model, pkl, device_config.gpu_id)
            t2 = time.time()
            logger.warning("[fluidckpt] rank %d attached %d tensors / %.2f GiB: skeleton %.2fs, "
                           "attach %.2fs", tp, info["tensors"], info["bytes"] / 2**30, t1 - t0, t2 - t1)
            return model.eval()

        L.DummyModelLoader.load_model = load_model
        return

    if mode == "capture":
        orig = L.DefaultModelLoader.load_model
        L.DefaultModelLoader._fluidckpt_original_load_model = orig

        def load_model(self, *, model_config, device_config):
            from fluidckpt.serving.measure import emit
            emit(self.load_config.tp_rank, "source_load_begin")
            model = orig(self, model_config=model_config, device_config=device_config)
            tp = self.load_config.tp_rank
            emit(tp, "source_load_end")
            export_storages(model, tp, device_config.gpu_id, "capture")
            if os.environ.get("FLUIDCKPT_CAPTURE_WAIT") == "1":
                # hold until the keeper has copied this rank out; nothing may run a forward
                # (and possibly crash the process) before the snapshot is safe in DRAM
                info = _wait_done(tp, float(os.environ.get("FLUIDCKPT_TIMEOUT", "3600")), suffix="captured")
                logger.warning("[fluidckpt] rank %d captured by keeper: %s", tp, json.dumps(info))
            return model

        L.DefaultModelLoader.load_model = load_model

    else:
        def load_model(self, *, model_config, device_config):
            from fluidckpt.serving.measure import emit
            cold_ranks = {int(rank) for rank in os.environ.get('FLUIDCKPT_COLD_RANKS', '').split(',') if rank}
            if self.load_config.tp_rank in cold_ranks:
                import copy
                from sglang.srt.configs.load_config import LoadFormat
                config = copy.copy(self.load_config)
                config.load_format = LoadFormat.AUTO
                loader = L.DefaultModelLoader(config)
                emit(config.tp_rank, 'source_load_begin', recovery_fallback=True)
                model = L.DefaultModelLoader._fluidckpt_original_load_model(
                    loader, model_config=model_config, device_config=device_config)
                emit(config.tp_rank, 'source_load_end', recovery_fallback=True)
                export_storages(model, config.tp_rank, device_config.gpu_id, 'capture')
                _wait_done(config.tp_rank, float(os.environ.get('FLUIDCKPT_TIMEOUT', '7200')), suffix='captured')
                return model.eval()
            emit(self.load_config.tp_rank, "model_layout_begin")
            t0 = time.time()
            quant_config = L._get_quantization_config(model_config, self.load_config)
            manifest = os.environ.get("FLUIDCKPT_MANIFEST", "")
            layout_path = os.path.join(os.path.dirname(manifest),
                                       f"layout.rank{self.load_config.tp_rank}.json")
            if os.path.isfile(layout_path):
                from fluidckpt.serving.layout import materialize
                with L.set_default_torch_dtype(model_config.dtype), torch.device("meta"):
                    model = L._initialize_model(model_config, self.load_config, quant_config)
                with open(layout_path) as stream:
                    layout = json.load(stream)
                materialize(model, layout, device_config.device)
                tp = self.load_config.tp_rank
                emit(tp, "model_layout_end")
                export_storages(model, tp, device_config.gpu_id, "restore")
                _wait_done(tp, float(os.environ.get("FLUIDCKPT_TIMEOUT", "3600")))
                emit(tp, "target_filled")
                return model.eval()
            with L.set_default_torch_dtype(model_config.dtype):
                with torch.device(device_config.device):
                    model = L._initialize_model(model_config, self.load_config, quant_config)
                for _, module in model.named_modules():
                    qm = getattr(module, "quant_method", None)
                    if qm is None:
                        continue
                    if hasattr(module, "is_weights_quantized") and module.is_weights_quantized():
                        continue
                    qm.process_weights_after_loading(module)
                # Rebuild the MLA fused weights the skeleton cannot derive. `_post_load_weights`
                # is never called on this path: the dummy loader would invoke it *after*
                # `process_weights_after_loading`, i.e. on the transposed kv_b_proj, which raises.
                # The real loader derives these from the untransposed tensor, so the only faithful
                # source is the capture. Allocate each one at the captured shape/dtype and let the
                # keeper fill it exactly as it fills a param.
                _install_derived(model, self.load_config.tp_rank, device_config.gpu_id)
                t1 = time.time()
                tp = self.load_config.tp_rank
                emit(tp, "model_layout_end")
                export_storages(model, tp, device_config.gpu_id, "restore")
                done = _wait_done(tp, float(os.environ.get("FLUIDCKPT_TIMEOUT", "3600")))
                emit(tp, "target_filled")
                t2 = time.time()
            logger.warning("[fluidckpt] rank %d restored: skeleton %.2fs, fill-wait %.2fs, keeper %s",
                           tp, t1 - t0, t2 - t1, json.dumps(done))
            return model.eval()

        L.DummyModelLoader.load_model = load_model
        # DummyModelLoader refuses model_loader_extra_config; nothing else to relax.
