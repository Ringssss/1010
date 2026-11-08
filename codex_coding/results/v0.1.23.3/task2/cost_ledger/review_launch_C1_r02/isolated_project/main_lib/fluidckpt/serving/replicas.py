"""Worker-local, versioned replicas of logical weight regions with read leases."""
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import json
import threading
import time


def identity(meta, index):
    fields = {k: meta.get(k) for k in ("model_identity", "tp_size", "tp_rank", "tensors")}
    fields["index"] = index
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


@dataclass
class Replica:
    replica_id: str
    generation: int
    identity: str
    rank: int
    logical_offset: int
    length: int
    numa: int
    worker: int
    tensor: object = field(repr=False)
    owner: object = field(default=None, repr=False)
    state: str = "VALID"
    leases: int = 0
    build: dict = field(default_factory=dict)

    def descriptor(self):
        return {"replica_id": self.replica_id, "generation": self.generation, "identity": self.identity,
                "rank": self.rank, "logical_offset": self.logical_offset, "length": self.length,
                "numa": self.numa, "worker": self.worker, "state": self.state, "leases": self.leases,
                "owned_allocation_bytes": self.owner.allocated_bytes if self.owner else 0,
                "build": self.build}


class ReplicaCatalog:
    """Only this worker may dereference tensor pointers. Descriptors contain no raw pointers."""
    def __init__(self, extra_budget=8 << 30):
        self.entries = {}
        self.lock = threading.RLock()
        self.extra_budget = extra_budget

    def publish(self, replica_id, model_identity, rank, offset, tensor, numa, worker, owner=None, build=None):
        if type(offset) is not int or offset < 0 or tensor.numel() <= 0:
            raise ValueError("invalid replica extent")
        with self.lock:
            previous = self.entries.get(replica_id)
            if previous and previous.state != "RELEASED":
                raise ValueError("replica id already exists")
            if owner and (owner.allocated_bytes is None or self.owned_bytes() + owner.allocated_bytes > self.extra_budget):
                raise MemoryError("extra replica budget exceeded")
            entry = Replica(replica_id, previous.generation + 1 if previous else 1,
                            model_identity, rank, offset, tensor.numel(), numa, worker, tensor,
                            owner=owner, build=build or {})
            self.entries[replica_id] = entry
            return entry.descriptor()

    def owned_bytes(self):
        return sum(e.owner.allocated_bytes for e in self.entries.values()
                   if e.owner and e.state != "RELEASED")

    def inventory(self, valid_only=True):
        with self.lock:
            return [e.descriptor() for e in self.entries.values() if not valid_only or e.state == "VALID"]

    def _validate(self, op):
        e = self.entries.get(op["replica_id"])
        if e is None or e.state != "VALID" or e.generation != op["generation"]:
            raise ValueError("missing, retired, or stale replica")
        if e.identity != op["identity"] or e.rank != op["tp"] or e.worker != op["dst_w"]:
            raise ValueError("wrong model, rank, or inaccessible owner")
        off, src, length = op["off"], op["source_offset"], op["n"]
        if any(type(v) is not int for v in (off, src, length)) or length <= 0:
            raise ValueError("invalid integer byte range")
        if src != off - e.logical_offset or src < 0 or src + length > e.length:
            raise ValueError("source offset or coverage mismatch")
        return e

    @contextmanager
    def acquire(self, ops):
        with self.lock:
            entries = {op["replica_id"]: self._validate(op) for op in ops}
            for entry in entries.values():
                entry.leases += 1
        try:
            yield entries
        finally:
            with self.lock:
                for entry in entries.values():
                    entry.leases -= 1
                    if entry.leases == 0 and entry.state == "RETIRING":
                        self._release(entry)

    def _release(self, entry):
        entry.tensor = None
        if entry.owner:
            entry.owner.close()
            entry.owner = None
        entry.state = "RELEASED"

    def retire(self, replica_id):
        with self.lock:
            entry = self.entries[replica_id]
            if entry.state == "RELEASED":
                return entry.descriptor()
            entry.state = "RETIRING"
            if entry.leases == 0:
                self._release(entry)
            return entry.descriptor()

    def retire_identity(self, model_identity):
        with self.lock:
            entries = [e for e in self.entries.values() if e.identity == model_identity]
            if any(e.leases for e in entries):
                raise RuntimeError("cannot release primary snapshot with active source leases")
            for e in entries:
                self.retire(e.replica_id)

    def clone(self, source_id, replica_id, offset, length, numa):
        """Copy and verify a partial replica before publishing it; construction is serialized."""
        import torch
        from fluidckpt.memory import HostAllocation
        if type(length) is not int or length <= 0:
            raise ValueError("positive replica length required")
        with self.lock:
            source = self.entries[source_id]
            op = {"replica_id": source_id, "generation": source.generation, "identity": source.identity,
                  "tp": source.rank, "dst_w": source.worker, "off": offset,
                  "source_offset": offset - source.logical_offset, "n": length}
            rounded = (length + 4095) // 4096 * 4096
            if self.owned_bytes() + rounded > self.extra_budget:
                raise MemoryError("extra replica budget exceeded before allocation")
            owner = None
            start = time.monotonic()
            with self.acquire([op]):
                try:
                    owner = HostAllocation(length, "numa_registered", numa)
                    allocated = time.monotonic()
                    # Bounded temporary views are released before any rollback unmaps memory.
                    for pos in range(0, length, 64 << 20):
                        n = min(64 << 20, length - pos)
                        owner.tensor[pos:pos+n].copy_(source.tensor[op["source_offset"]+pos:op["source_offset"]+pos+n])
                    copied = time.monotonic()
                    for pos in range(0, length, 64 << 20):
                        n = min(64 << 20, length - pos)
                        if not torch.equal(owner.tensor[pos:pos+n], source.tensor[op["source_offset"]+pos:op["source_offset"]+pos+n]):
                            raise RuntimeError("replica verification failed")
                    built = {"allocate_s": allocated-start, "copy_s": copied-allocated,
                             "verify_s": time.monotonic()-copied, "bytes_verified": length,
                             "allocation": owner.info(), "source_replica": source_id}
                    return self.publish(replica_id, source.identity, source.rank, offset, owner.tensor,
                                        numa, source.worker, owner=owner, build=built)
                except BaseException:
                    if owner is not None:
                        owner.close()
                    raise
