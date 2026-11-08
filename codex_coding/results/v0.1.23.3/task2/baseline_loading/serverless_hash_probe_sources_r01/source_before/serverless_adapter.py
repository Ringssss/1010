"""Upstream ServerlessLLM Store data plane targeting existing engine IPC storages.

The frozen loader owns the disk, pinned-memory and chunk-dispatch pipeline.
This adapter supplies destination handles and exact processed-storage offsets.
It does not represent ServerlessLLM's placement scheduler or serving frontend.
"""
from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path
import time
import uuid

UPSTREAM_SHA = "9f50241baa5386e06a9321c51f19a9ef5f964c2b"


def raw_ipc_handle(serialized):
    """Decode the observed PyTorch 2.10 malloc framing into upstream's raw HIP IPC ABI."""
    encoded = bytes(serialized)
    if len(encoded) == 64:
        return encoded, "legacy_raw64"
    if len(encoded) == 66 and encoded[:2] == b"\x02c":
        return encoded[2:], "pytorch_version2_malloc_66"
    raise ValueError("unsupported IPC handle framing; only raw64 or version2 malloc are accepted")


def destination_plan(index, export):
    """Preserve PyTorch IPC allocation offsets while deduplicating memory handles."""
    entries = {row["name"]: row for row in export["entries"]}
    if set(entries) != set(index):
        raise ValueError("source index and target storage names differ")
    handles = []
    handle_indices = {}
    chunks = []
    cursor = 0
    device = None
    formats = {}
    for name, (offset, size) in sorted(index.items(), key=lambda row: row[1][0]):
        if offset != cursor or size <= 0:
            raise ValueError("source storage layout must be contiguous and nonempty")
        row = entries[name]
        handle = row["handle"]
        # _share_cuda_: device, raw IPC handle, storage_size_bytes, allocation_offset_bytes, ...
        if len(handle) != 8 or row["nbytes"] != size or handle[2] != size:
            raise ValueError(f"IPC storage byte count mismatch: {name}")
        if device is None:
            device = handle[0]
        if handle[0] != device or handle[3] < 0:
            raise ValueError("a rank must target one device with valid allocation offsets")
        raw, frame = raw_ipc_handle(handle[1])
        formats[frame] = formats.get(frame, 0) + 1
        if raw not in handle_indices:
            handle_indices[raw] = len(handles)
            handles.append(raw)
        chunks.append((offset, size, handle[3], handle_indices[raw]))
        cursor += size
    if not chunks or export["meta"].get("bytes", cursor) != cursor:
        raise ValueError("empty or incomplete target storage export")
    return {"device": device, "handles": handles, "chunks": chunks,
            "logical_bytes": cursor, "storage_count": len(chunks),
            "allocation_count": len(handles),
            "ipc_handle_formats": formats,
            "nonzero_allocation_offsets": sum(chunk[2] != 0 for chunk in chunks)}


class UpstreamStoreLoader:
    """Load processed weights through the actual upstream C++ pipeline."""

    def __init__(self, source_checkout, storage_path, pool_bytes, chunk_bytes=32 << 20,
                 io_threads=4):
        import sys
        source = Path(source_checkout).resolve() / "sllm_store"
        sys.path.insert(0, str(source))
        import torch
        self.torch = torch
        self.backend = importlib.import_module("sllm_store._checkpoint_store")
        self.native = importlib.import_module("sllm_store._C")
        if not str(Path(self.backend.__file__).resolve()).startswith(str(source)):
            raise RuntimeError("a shared installation shadowed the isolated loader")
        self.chunk_bytes = chunk_bytes
        self.pool_bytes = ((pool_bytes + chunk_bytes - 1) // chunk_bytes) * chunk_bytes
        started = time.time()
        self.store = self.backend.CheckpointStore(str(storage_path), self.pool_bytes,
                                                  io_threads, chunk_bytes)
        self.setup_s = time.time() - started
        self.device_uuids = self.native.get_device_uuid_map()

    def start_rank(self, model_path, index, export):
        plan = destination_plan(index, export)
        started = time.time()
        file_bytes = self.store.register_model_info(model_path)
        if file_bytes < plan["logical_bytes"] or file_bytes - plan["logical_bytes"] >= 4096:
            raise ValueError("upstream file size differs from logical payload plus sector padding")
        if file_bytes > self.pool_bytes:
            raise ValueError("upstream requires a full rank host allocation")
        if self.store.load_model_from_disk_async(model_path) != 0:
            raise RuntimeError("upstream disk scheduling failed")
        chunks = []
        for src, size, dst, handle_id in plan["chunks"]:
            chunk = self.backend.MemCopyChunk()
            chunk.src_offset, chunk.size = src, size
            chunk.dst_offset, chunk.handle_idx = dst, handle_id
            chunks.append(chunk)
        device_uuid = self.device_uuids[plan["device"]]
        replica = str(uuid.uuid4())
        if self.store.load_model_from_mem_async(model_path, replica,
                {device_uuid: plan["handles"]}, {device_uuid: chunks}) != 0:
            raise RuntimeError("upstream GPU scheduling failed")
        return {"model_path": model_path, "replica_uuid": replica,
                "load_begin_unix_s": started, "file_bytes": file_bytes,
                "logical_bytes": plan["logical_bytes"],
                "padding_bytes": file_bytes-plan["logical_bytes"],
                "storage_count": plan["storage_count"],
                "allocation_count": plan["allocation_count"],
                "nonzero_allocation_offsets": plan["nonzero_allocation_offsets"],
                "ipc_handle_formats": plan["ipc_handle_formats"],
                "destination_chunk_geometry": [{"file_offset": src, "logical_bytes": size,
                    "allocator_offset": dst, "allocation_index": handle_id}
                    for src, size, dst, handle_id in plan["chunks"]]}

    def wait_rank(self, record):
        if self.store.wait_model_in_gpu(record["model_path"], record["replica_uuid"]) != 0:
            raise RuntimeError("upstream GPU completion failed")
        record["store_ready_unix_s"] = time.time()
        record["store_load_s"] = record["store_ready_unix_s"]-record["load_begin_unix_s"]
        return record

    def close(self):
        self.store.clear_mem()


def hash_target(index, export, chunk_bytes=64 << 20):
    """Hash every logical payload byte in file order after upstream completion."""
    import torch
    entries = {row["name"]: row for row in export["entries"]}
    digest = hashlib.sha256()
    for name, (_, size) in sorted(index.items(), key=lambda row: row[1][0]):
        row = entries[name]
        storage = torch.UntypedStorage._new_shared_cuda(*row["handle"])
        tensor = torch.empty(0, dtype=torch.uint8, device=f"cuda:{row['handle'][0]}").set_(storage)
        if tensor.numel() != size:
            raise ValueError("imported storage size mismatch")
        for offset in range(0, size, chunk_bytes):
            digest.update(tensor[offset:offset+chunk_bytes].cpu().numpy().tobytes())
        del tensor, storage
    return digest.hexdigest()


def atomic_json(path, data):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)
