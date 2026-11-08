"""Attach a fresh model skeleton to weights a live producer already holds in HBM, over HIP IPC.

This is the zero-copy warm start: the producer loaded the checkpoint normally and still owns the
post-processed storages, so a second process maps those exact pages instead of reading 34 GiB off
storage. No byte is copied, which is why this is the strongest single-node baseline there is.

Two consequences shape how it has to be used:

  * The producer must stay alive. An IPC handle names physical pages owned by another process; when
    that process exits the mapping is dead. A crashed or rolled-over server cannot be served this
    way, and that is the regime the keeper exists for.

  * The consumer must not allocate a full skeleton. Both processes share one GPU, so two copies of
    a model that is 34 GiB per rank do not fit in a 64 GiB card. The consumer therefore builds its
    modules on the meta device (structure only, no storage) and binds the producer's storages in.
    It also skips `process_weights_after_loading` entirely: the producer's storages are already
    post-processed, and re-running the transform on attached weights would corrupt them.

Attachment is exact: every tensor is rebuilt with its recorded dtype, shape, stride and storage
offset, so parameters that shared one storage keep sharing it.
"""
from __future__ import annotations

import functools
import pickle

import torch


def _parent(model, name):
    """(module owning `name`, final attribute name)."""
    parts = name.split(".")
    return functools.reduce(getattr, parts[:-1], model), parts[-1]


def read(pkl_path):
    with open(pkl_path, "rb") as f:
        return pickle.load(f)


def attach(model, pkl_path, gpu_id, log=None):
    """Bind every tensor the producer exported onto `model`, moving zero bytes.

    One storage is mapped per unique producer storage and reused by every tensor that pointed at
    it, so aliasing survives the attach.

    The mapped bytes come from another process's segment, so PyTorch's caching allocator has no
    idea they exist -- `torch.cuda.memory_allocated` reports ~0 while 33 GiB of foreign pages sit in
    the card. SGLang then sizes its KV pool and mamba state from the free memory it can see, hands
    out blocks that overlap the mapped weights, and the first forward de-references a bad address.
    Registering the mapped segments as allocator-visible memory is what makes the accounting honest;
    the returned byte count is also what the caller subtracts from its budget.
    """
    d = read(pkl_path)
    # The capture pickle is {"meta": {...}, "entries": [...]}; the per-tensor layout (dtype,
    # shape, stride, offset and which storage it shares) lives under meta["tensors"].
    by_name = {e["name"]: e for e in d["entries"]}
    storages = {}
    buffers = {}
    for tensor in d['meta']['tensors']:
        sname = tensor['storage_name']
        ent = by_name.get(sname)
        if ent is None:
            raise KeyError(f"producer snapshot has no storage {sname!r}")
        mapping_key = tuple(ent['handle'])
        if mapping_key not in storages:
            storage = torch.UntypedStorage._new_shared_cuda(*ent['handle'])
            storages[mapping_key] = torch.empty(0, dtype=torch.uint8,
                device=f'cuda:{gpu_id}').set_(storage)
        buffers[sname] = storages[mapping_key]
    from fluidckpt.serving.ipc_layout import bind_imported
    bind_imported(model, d['meta']['tensors'], by_name, buffers)
    n = len(d['meta']['tensors'])
    if log:
        log(f"attached {n} tensors over {len(storages)} storages from {pkl_path}")
    meta_left = unbound(model, plain=True)
    if meta_left:
        # Print the full list before failing: the first run of this check is the only cheap chance
        # to see the whole set, and a crash in the scheduler buries anything logged after it.
        print(f"[fluidckpt] {len(meta_left)} tensors unbound after attach, first 40: "
              f"{meta_left[:40]}", flush=True)
        raise RuntimeError(
            f"{len(meta_left)} tensors are still on the meta device after attach, so their "
            f"data_ptr is 0 and the first kernel that reads one faults at a low address")
    return {"tensors": n, "storages": len(storages),
            "bytes": sum(e["nbytes"] for e in d["entries"])}


def unbound(model, plain=False):
    """Names of tensors the skeleton exposes that never got real storage.

    Walking the skeleton rather than the snapshot is deliberate: the snapshot lists what the
    producer had, and the failure mode being hunted is the reverse -- something the consumer
    *needs* that the producer never exported, or that the dummy path leaves unbuilt.

    `plain=True` also walks tensors held as bare module attributes. Those are invisible to
    `named_parameters()`/`named_buffers()`, so a capture that only walks the two registry calls
    silently omits them -- and because the skeleton is built under `torch.device("meta")`, every
    one of them comes out as a meta tensor with `data_ptr() == 0`. That is precisely the shape of
    a kernel faulting at a near-null address.
    """
    out = []
    for name, t in list(model.named_parameters()) + list(model.named_buffers()):
        if t.device.type == "meta" or (t.numel() and t.data_ptr() == 0):
            out.append(name)
    if plain:
        for mod_name, mod in model.named_modules():
            for attr, v in list(vars(mod).items()):
                if (isinstance(v, torch.Tensor) and not isinstance(v, torch.nn.Parameter)
                        and attr not in mod._buffers):
                    if v.device.type == "meta" or (v.numel() and v.data_ptr() == 0):
                        out.append(f"{mod_name}.{attr} (plain attr)")
    return out
