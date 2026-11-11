"""Scatter a model's chunks across the cluster, then pull them back from several sources at once.

The shape of the system, in one file's worth of steps:

  1. `chunks.build_manifest` cuts the model into content-addressed blocks. The user's files are
     never modified -- a block is just a byte range that remembers which tensors it covers.
  2. Blocks are placed **wherever there is room** on any node's store (SSD here, DRAM in the
     production path). Placement is by free space, so a block from the middle of a file can sit on
     node30 while its neighbours sit on node28: fragmentation happens in placement, not in format.
  3. Restore pulls every block back from whichever node holds it, several sources in parallel, and
     reassembles the original files byte for byte.

Commands:
  place    coordinator: build the manifest, assign chunks to nodes by free space, write plans
  push     source: broadcast the chunks listed in its plan to a peer
  pull     destination: receive them into its store
  verify   reassemble from local chunks and check the sha256 of every file
"""
import argparse
import json
import os
import sys
import threading

MiB = 1 << 20
REPO = "/home/zhujianian/zhujianian/fluidckpt_zjn"


def read_fully(fh, view):
    """Fill `view` completely. A single raw readinto() can return short (measured at exactly
    2 GiB on this node's local NVMe), so the sender must loop instead of trusting one call."""
    total, off = len(view), 0
    while off < total:
        got = fh.readinto(view[off:])
        if not got:
            raise IOError(f"short read at {off}/{total}")
        off += got
    return off


# ---------------------------------------------------------------- pinned pool
# One persistent pinned host buffer per size, alive for the life of the process. Pinning a
# model-sized buffer is not free: measured on this node, torch.empty(N, pin_memory=True) costs
# 0.07 s at 256 MiB, 0.09 s at 512 MiB and 0.61 s at 1177 MiB, while the read that follows
# measures 23 ms per chunk. So the per-restore pin is the fixed cost in front of the first
# broadcast, not the disk: a standalone sequential read of the same 21 chunk files ran at
# 4.56 GB/s (0.25 s for 1177.7 MiB), and the sender's steady state after the first chunk is
# 9-28 ms of host wait per chunk. In the product the sender's source IS a resident pinned pool
# -- that is what keeping a hot set alive means -- so the buffer is allocated once and reused
# across restores. A second restore in the same process reports hit=True and pays nothing.
_PINNED_POOL = {}


def pinned_buffer(nbytes):
    """Return a persistent pinned uint8 buffer of exactly `nbytes`, allocated at most once per
    process. Callers must treat the contents as undefined on the first (miss) call."""
    import torch
    buf = _PINNED_POOL.get(nbytes)
    if buf is None:
        buf = torch.empty(nbytes, dtype=torch.uint8, pin_memory=True)
        _PINNED_POOL[nbytes] = buf
    return buf


# ---------------------------------------------------------------- place
def cmd_place(a):
    sys.path.insert(0, os.path.join(REPO, "main_lib"))
    from fluidckpt.live.chunks import build_manifest, save_manifest

    os.makedirs(a.out, exist_ok=True)
    m = build_manifest(a.model_dir, chunk_bytes=a.chunk_mib * MiB)
    save_manifest(m, os.path.join(a.out, "manifest.json"))

    nodes = a.nodes.split(",")
    free = {n: int(x) for n, x in (p.split("=") for p in a.free.split(","))}
    # Deterministic placement: walk the chunks in order, give each to the node with the largest
    # remaining quota. That spreads a file's chunks round-robin over the cluster, which is exactly
    # the fragmentation the restore has to cope with.
    quota = {n: int(free[n] * a.fill) for n in nodes}
    spent = 0
    assign = {n: [] for n in nodes}
    plan = {}
    # round-robin by quota (balanced) or always take the largest remaining (free-space)
    order = sorted(range(len(m.chunks)), key=lambda i: -m.chunks[i].nbytes)
    rr = 0
    for i in order:
        c = m.chunks[i]
        if c.nbytes > max(quota.values()):
            raise SystemExit(f"chunk {c.nbytes} larger than every node's quota")
        if a.policy == "free":
            target = max(nodes, key=lambda n: quota[n])
        else:
            candidates = [n for n in nodes if quota[n] >= c.nbytes]
            if not candidates:
                raise SystemExit("cluster quota exhausted")
            target = candidates[rr % len(candidates)]
            rr += 1
        quota[target] -= c.nbytes
        spent += c.nbytes
        assign[target].append(c.id)
        plan[c.id] = [target]

    # One replica on a second node for a fraction of chunks: this is the redundancy that lets a
    # restore survive a node being taken over by another tenant.
    if a.replicas > 1:
        for cid, holders in sorted(plan.items()):
            c = next(c for c in m.chunks if c.id == cid)
            options = [n for n in nodes if n not in holders and quota[n] >= c.nbytes]
            if not options:
                continue
            target = max(options, key=lambda n: free[n])
            quota[target] -= c.nbytes
            spent += c.nbytes
            assign[target].append(cid)
            holders.append(target)

    for n in nodes:
        with open(os.path.join(a.out, f"assign_{n}.json"), "w") as fh:
            json.dump(assign[n], fh)
    with open(os.path.join(a.out, "plan.json"), "w") as fh:
        json.dump({"model": m.model, "chunk_bytes": m.chunk_bytes,
                   "file_size": sum(f["size"] for f in m.files.values()),
                   "plan": plan,
                   "per_node": {n: {"chunks": len(assign[n]),
                                    "bytes_sum": None} for n in nodes}}, fh)

    print(json.dumps({
        "event": "placed", "model": m.model, "chunks": len(m.chunks),
        "unique": len({c.id for c in m.chunks}),
        "model_MiB": round(sum(f["size"] for f in m.files.values()) / MiB, 1),
        "copies_MiB": round(spent / MiB, 1),
        "dedup_saved_MiB": round((len(m.chunks) - len({c.id for c in m.chunks})) * m.chunk_bytes / MiB, 1),
        "per_node_chunks": {n: len(assign[n]) for n in nodes},
        "manifest_s": round(m.build.get("seconds", 0), 2),
    }), flush=True)


# ---------------------------------------------------------------- seed
def cmd_seed(a):
    """Write this node's assigned chunks into its LOCAL store (the park path, no fabric)."""
    import time
    sys.path.insert(0, os.path.join(REPO, "main_lib"))
    from fluidckpt.live.chunks import DirChunkStore, load_manifest

    m = load_manifest(os.path.join(a.manifest_dir, "manifest.json"))
    want = set(json.load(open(a.chunks)))
    store = DirChunkStore(a.store)
    by_id = {c.id: c for c in m.chunks}
    t0 = time.time()
    written = 0
    for cid in want:
        if store.has(cid):
            continue
        c = by_id[cid]
        path = os.path.join(a.model_dir, c.file)
        with open(path, "rb") as fh:
            fh.seek(c.offset)
            data = fh.read(c.nbytes)
        if hashlib_sha(data) != cid:
            raise SystemExit(f"chunk {cid[:12]} content mismatch while seeding")
        store.put(cid, data)
        written += len(data)
    dt = time.time() - t0
    print(json.dumps({"event": "seeded", "node_chunks": len(want), "written_MiB": round(written / MiB, 1),
                      "seconds": round(dt, 3),
                      "GBps": round(written / dt / 1e9, 2) if dt else None,
                      "store_MiB": round(store_nbytes(store) / MiB, 1)}), flush=True)


def hashlib_sha(data):
    import hashlib
    return hashlib.sha256(data).hexdigest()


def store_nbytes(store):
    total = 0
    for dirpath, _d, names in os.walk(store.root):
        for n in names:
            if not n.endswith(".tmp"):
                total += os.path.getsize(os.path.join(dirpath, n))
    return total


# ---------------------------------------------------------------- fetchplan
def cmd_fetchplan(a):
    """For a destination, decide which holder feeds each chunk and group the work per source."""
    sys.path.insert(0, os.path.join(REPO, "main_lib"))
    from fluidckpt.live.chunks import load_manifest

    md = os.path.join(a.manifest_dir, "manifest.json")
    m = load_manifest(md)
    plan = json.load(open(os.path.join(a.manifest_dir, "plan.json")))["plan"]
    dst = a.dst_node
    per_src = {}
    already = set(a.have or [])
    for c in m.chunks:
        if c.id in already:
            continue
        holders = [h for h in plan[c.id] if h != dst]
        if not holders:
            raise SystemExit(f"chunk {c.id[:12]} has no holder outside {dst}")
        # spread the load: pick the holder currently carrying the least
        src = min(holders, key=lambda h: sum(1 for v in per_src.get(h, [])))
        per_src.setdefault(src, []).append(c.id)
    os.makedirs(a.out, exist_ok=True)
    for src, ids in per_src.items():
        with open(os.path.join(a.out, f"fetch_{src}.json"), "w") as fh:
            json.dump(ids, fh)
    print(json.dumps({"event": "fetchplan", "dest": dst,
                      "sources": {k: len(v) for k, v in per_src.items()},
                      "chunks_to_move": sum(len(v) for v in per_src.values())}), flush=True)


# ---------------------------------------------------------------- move
def cmd_move(a):
    """Move the chunks in `--chunks` between two nodes, landing them in the destination's store.

    Allocation and synchronisation follow the rules measured on this fabric:
      * every *device* buffer is created before the process group; first-touch allocation with a
        live communicator deadlocks;
      * no pinned host memory is registered anywhere near the collectives on the receiving side.
        The sender's ONE pinned host buffer, sized to the whole request, is allocated after the
        communicator is live and past its warm-up collective -- see the hypothesis note below;
      * copies and broadcasts all run on the default stream, with no side streams and no events;
      * the sender never waits on its own collective -- the receiver acks over TCP instead.

    Third-hypothesis note. Earlier versions hung with every collective enqueued and returning but
    the post-loop torch.cuda.synchronize() (and copy_s.synchronize()) never returning. Two failed
    hypotheses already ruled things out: the post-loop sync is not itself the culprit (a
    stream-scoped sync hangs identically), and async D2H to pinned on a side stream is not either
    (the D2D-hop version with no side stream hung too). What every failing version had and the
    working one -- restore_chunks.py, 19 GiB at 36.35 GB/s -- did not, is a pinned host
    registration held across the collectives. So the pinned buffer now appears only on the sender,
    only after stage("warm"), and the receiver never allocates pinned memory at all.
    If this still hangs, the next step is to run the working script unchanged, which isolates a
    changed machine from a changed script.
    """
    import datetime
    import socket
    import time

    import torch
    import torch.distributed as dist

    sys.path.insert(0, os.path.join(REPO, "main_lib"))

    # The subcommand *is* the role: `chunk_fluid.py push ...` / `pull ...`. Reading only --role
    # left both ends in the pull branch, each waiting for the other's size manifest, which is why
    # every pair failed with a missing sizes.json.
    role = a.role or a.cmd
    # --source-pool only makes sense on the sender: it replaces the chunk files with a resident
    # pinned buffer that is assumed to already hold the model bytes. On the receiver there is no
    # source to pool, so the flag is a launch-line error rather than a silent no-op.
    if a.source_pool and role != "push":
        raise SystemExit("--source-pool is sender-only (push rank); it does not apply to pull")
    os.makedirs(a.out, exist_ok=True)
    ids = json.load(open(a.chunks))
    chunk = a.chunk_mib * MiB
    rank = 0 if role == "push" else 1
    torch.cuda.set_device(a.gpu)
    dev = torch.device("cuda", a.gpu)

    def path_of(cid):
        return os.path.join(a.store, cid[:2], cid)

    # ---- sizes travel in the CLI-adjacent header file so both sides agree before allocating
    if role == "push":
        sizes = {cid: os.path.getsize(path_of(cid)) for cid in ids}
        with open(os.path.join(a.out, "sizes.json"), "w") as fh:
            json.dump(sizes, fh)
    else:
        sp = os.path.join(a.out, "sizes.json")
        deadline = time.time() + 120
        while not os.path.exists(sp) and time.time() < deadline:
            time.sleep(0.3)
        sizes = {k: int(v) for k, v in json.load(open(sp)).items()}
    total = sum(sizes[c] for c in ids)

    # ---- all buffers before the group
    # Two staging buffers for the *receiver*, which cannot broadcast straight into its final
    # layout and so needs a ping-pong pair: the broadcast lands in bufs[k], the D2D copy moves it
    # into `payload`, and the next broadcast uses the other buffer. The sender no longer needs
    # them -- it broadcasts out of its HBM-resident `payload` -- but they are still allocated on
    # both ranks so the two sides run identical code up to the loop. They are allocated here,
    # before the communicator is live, for the same reason as everything else on this driver:
    # first-touch allocation while a collective is pending deadlocks.
    #
    # Deliberately NO pinned host buffer here. A pinned registration (cudaHostRegister) held
    # across the collectives is the one thing common to every version that hung and absent from
    # restore_chunks.py, which works. The sender allocates its pinned `host` after stage("warm");
    # the receiver never allocates pinned memory at all.
    bufs = [torch.empty(chunk, dtype=torch.uint8, device=dev) for _ in range(2)]
    torch.cuda.synchronize()
    srv = None
    if role == "push":
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", a.ack_port))
        srv.listen(1)

    def stage(tag, **kw):
        print(json.dumps({"stage": tag, "role": role, "t": round(time.time() % 100000, 3), **kw}),
              flush=True)

    stage("pre_init", chunks=len(ids), MiB=round(total / MiB, 1))
    dist.init_process_group("nccl", init_method=f"tcp://{a.master}", rank=rank, world_size=2,
                            timeout=datetime.timedelta(seconds=600))
    stage("inited")
    pg = dist.new_group([0, 1])
    stage("group")
    dist.broadcast(torch.zeros(1, device=dev), src=0, group=pg)
    stage("warm")

    # ---- read the chunks off local store into the pinned buffer (sender only)
    # Without this the broadcast ships whatever the allocator handed back: the receiver then writes
    # uninitialised bytes into its store and still acks success.
    #
    # The pinned buffer is allocated HERE, not before the group: the communicator is live and past
    # its warm-up collective, exactly as in restore_chunks.py, so no pinned registration is held
    # across a collective. The receiver never gets one at all.
    #
    # The read is a *background thread*, not a serial prologue. Measured on one run -- 1177.7 MiB,
    # node30 -> node27, 256 MiB staging -- reading every chunk before the loop cost `loaded` = 783 ms,
    # while the receiver's `probe_sync i=0` was 808 ms and every later chunk 5.9 ms, and `prefaulted`
    # was 1 ms. Page first-touch was not the cost: the receiver's first collective was simply waiting
    # for the sender to finish reading all of the model before the first broadcast could be served.
    # The fabric is ~24-42 GB/s once bytes are moving; the serial disk read in front of the loop was
    # the whole bottleneck, and every earlier "1.5-1.9 GB/s" number was a disk-read number.
    #
    # So the reader thread fills `host` chunk by chunk in order, setting one Event per chunk as that
    # chunk's bytes land, and the main thread only ever waits on `ready[i]` before staging and
    # broadcasting chunk i. Chunk i+1's read then overlaps chunk i's broadcast, and wall time drops
    # toward max(read of the first chunk, broadcast time) + the remaining reads instead of
    # read-everything-then-broadcast. `ready[0]` is the one unavoidable stall, and it is one chunk,
    # not the whole model.
    total_chunks = (total + chunk - 1) // chunk
    t_load = time.time()
    read_finish = []          # thread stamps its finish time here; read by the final event JSON
    read_err = []             # a read failure must not hang the loop -- it is re-raised below
    ready = [threading.Event() for _ in range(total_chunks)]
    fire_idx = [0]            # next loop-chunk index the reader has fully published

    if role == "push":
        # Persistent, process-lifetime pinned pool rather than a fresh pin per restore: the pin
        # itself is 0.61 s at 1177 MiB (see the note on pinned_buffer), against 23 ms/chunk for
        # the read and 9-28 ms/chunk of steady-state host wait. On a second restore in the same
        # process this is a dict lookup, which `hit` in the stage line records.
        t_pin = time.time()
        was_pinned = total in _PINNED_POOL
        host = pinned_buffer(total)
        stage("pin_pool", MiB=round(total / MiB, 1), hit=was_pinned,
              s=round(time.time() - t_pin, 3))
        host_view = memoryview(host.numpy())

        def read_chunks():
            """Fill `host` in the same fixed-size pieces the transfer loop consumes.

            The Events must follow the LOOP's chunk boundaries, not the file boundaries. The first
            version set `ready[i]` once per chunk FILE, which is wrong whenever a file is smaller
            than `chunk`: the loop's piece 0 spans several 60 MiB files, so `ready[0]` fired after
            the first 60 MiB and the sender broadcast ~196 MiB of uninitialised pool. That corrupted
            19 of 21 chunks on the --persist run while leaving chunk 0 plausible."""
            try:
                off = 0
                for i, cid in enumerate(ids):
                    n = sizes[cid]
                    with open(path_of(cid), "rb", buffering=0) as fh:
                        read_fully(fh, host_view[off:off + n])
                    off += n
                    # Publish every loop-chunk boundary this file completed.
                    fired = off // chunk
                    while fire_idx[0] < fired:
                        ready[fire_idx[0]].set()
                        fire_idx[0] += 1
            except BaseException as exc:       # noqa: BLE001 -- every failure must reach the main thread
                read_err.append(exc)
            finally:
                # Always publish and always finish: on error the loop must unblock, see read_err,
                # and re-raise instead of waiting forever on an Event nobody will set.
                for ev in ready:
                    ev.set()
                read_finish.append(time.time())

        if a.source_pool:
            # Steady-state keep-alive: the weights are already resident in the pinned pool, so the
            # only work left is moving them. No chunk file is opened and no reader thread is
            # started -- every Event is pre-set, so the loop's ready[i].wait() returns immediately
            # and behaves exactly as it does once a real read has finished a chunk. The buffer's
            # *contents* are deliberately unspecified here; this measures the transport path, not
            # the bytes, and the receiver acks on shape (sizes are still read from the store).
            for ev in ready:
                ev.set()
            stage("source_pool", MiB=round(total / MiB, 1), chunks=total_chunks)
        else:
            threading.Thread(target=read_chunks, name="chunk-reader", daemon=True).start()
            stage("reading", MiB=round(total / MiB, 1), chunks=total_chunks)

    # Device-side landing buffer, allocated AFTER warm on both ranks. This is the minimal
    # difference against restore_chunks.py, which allocates its payload before the group and works;
    # here we move it after, so the experiment changes exactly one variable. On the sender it is
    # the broadcast *source* (staged per chunk, in the loop); on the receiver it is the final
    # landing buffer.
    payload = torch.empty(total, dtype=torch.uint8, device=dev)

    # ---- staging now happens per chunk, inside the loop (sender only)
    # The loop below copies chunk i pinned->HBM immediately before broadcasting chunk i, so only the
    # H2D for the chunk currently on the wire sits between collectives. Earlier this file staged the
    # whole payload in one copy_ before the loop, which was the fix for the 1.5-1.9 GB/s interleaved
    # version -- but it also put the entire model's H2D *and* its entire disk read in front of the
    # first broadcast, which is what the 808 ms first `probe_sync` was actually measuring. Streaming
    # the reads is what removes that; the H2D is 43 GB/s at 256 MiB (27 ms for 1177.7 MiB measured
    # as `staged`) and is now overlapped with the broadcast that follows it.

    # ---- pre-fault the landing buffer (pull only, --prefault)
    # Experiment: the receiver's `payload` is fresh VA with no physical backing, and the measured
    # `probe_sync i=0` is 741 ms against 5.9-6.0 ms for every later chunk, i.e. one-off cost on the
    # first collective + D2D that writes into it. If that is first-touch page population, doing it
    # here moves the whole spike out of `dt` and the loop should run at line rate throughout.
    #
    # Strictly pull-only: on the sender `payload` is the broadcast source, filled per chunk from
    # the reader thread, so touching it here would be pointless. Strictly before t0: this is a host
    # driver deadlocks. It sits here -- after the warm-up collective, before the loop -- so no
    # collective of the transfer is in flight when it runs.
    if a.prefault and role == "pull":
        t_pf = time.time()
        payload.zero_()
        torch.cuda.synchronize()
        stage("prefaulted", MiB=round(total / MiB, 1), s=round(time.time() - t_pf, 3))

    # ---- the transfer
    # The sender's loop contains the collective plus the H2D for *that one chunk*, enqueued on the
    # default stream immediately before it, so chunk i+1's disk read (on the reader thread) overlaps
    # chunk i's broadcast. An earlier version put the H2D inside the loop without overlapping the
    # reads and reached only 1.64-1.94 GB/s; the measurement above shows that number was the serial
    # disk read, not the interleaved copy, and the pinned->HBM leg alone is 43 GB/s. What was
    # genuinely wrong with the first per-chunk-H2D version was reading the whole model first.
    #
    # The receiver keeps its double buffer: it still cannot broadcast in place into its final
    # layout, so chunk k lands in bufs[k] and is copied D2D into `payload`.
    #
    # Everything here runs on the current (default) stream: no side streams, no CUDA events, no
    # per-chunk allocation. Same-stream ordering already serialises each copy with the broadcast
    # around it. This is the shape bcast_restore.py/pair_bcast.py run at line rate on this fabric;
    # every version that added streams and CUDA events to it hung instead.
    #
    # The sender never waits on the host here: on this driver a host sync after a live collective
    # does not return for a large payload (15 GiB hangs, 1.4 GiB sometimes does not). It waits on
    # the receiver's TCP ack instead. The only host wait on the sender path is the reader thread's
    # Event, which is plain host memory and resolves as soon as the chunk's bytes are in `host`.
    #
    # The receiver runs in one of two completion modes, and `dt` for both is measured from the
    # same t0, so the numbers are directly comparable:
    #
    #   device_resident (default) -- "time until the weights are usable in HBM". The loop lands
    #       every byte in `payload`, the device-wide sync retires the last copy, and that is the
    #       end of the measurement. Nothing is copied to the host and nothing is written to the
    #       chunk store, because the MaaS requirement is a resident model, not a file.
    #
    #   persist (--persist) -- the same measurement plus landing the bytes in the destination's
    #       chunk store, via ONE synchronous pageable device->host copy afterwards. That copy is
    #       the expensive part and it is entirely ours, not the fabric's: pageable D2H measures
    #       ~1.4 GB/s on this stack, ~8x slower than pinned, so 368 MiB costs ~0.18-0.25 s on its
    #       own. The reference probe -- pair_bcast.py, which touches no host memory at all and
    #       syncs at the receiver -- moves 2 GiB cross-node at 45.02-45.35 GB/s on these same
    #       nodes. So the 0.9-1.34 GB/s this file used to report for 244-368 MiB was the host
    #       copy being counted as transfer time, not a slow fabric.
    # ---- repeat mode: N transfers in one process, steady state reported separately
    # A fresh process per restore charges the one-time costs (the 1177 MiB pinned allocation is
    # 0.61-0.64 s on its first call and ~0 on every later call in the same process -- see the
    # `pin_pool` marker and _PINNED_POOL) to a number that is then read as transfer time. Running
    # the timed region N times in one process separates the two: iteration 0 pays the cold cost,
    # iterations 1..N-1 are the steady state (hot pinned pool, resident source) that the
    # keep-alive design actually claims.
    #
    # Both ranks must run the SAME number of iterations or the broadcast collectives desynchronise
    # and the run hangs -- there is no negotiation of this value, it comes from the flag on both
    # launch lines. In particular the ack handshake moved inside this loop: the sender accepts one
    # connection per iteration and the receiver opens one per iteration, in lockstep.
    #
    # Everything outside this loop (process group, warm-up, buffer allocation, pinned pool) is
    # setup and is deliberately not repeated, and nothing inside it allocates: the same `payload`,
    # `bufs` and `host` are reused every iteration.
    if a.repeat < 1:
        raise SystemExit("--repeat must be >= 1")
    it_s = []
    for it in range(a.repeat):
        last = it == a.repeat - 1
        t0 = time.time()
        # Per-chunk instrumentation, receiver only. The measured shape: chunk 0 completes in ~8 ms
        # (~32 GB/s, line rate) and every later chunk takes ~165 ms (~1.5 GB/s), so the comparison
        # between the first chunk and the rest is the entire signal. Markers are therefore emitted for
        # the first 8 chunks plus the last one, not for all of them, to keep a long run readable.
        #
        # `d2d_issue_s` is the host-side enqueue cost of the copy_ call, NOT the GPU cost of the copy:
        # the call is non_blocking on the default stream and returns as soon as the kernel is queued.
        # A large d2d_issue_s means the copy itself is expensive; a small one with a large bcast_s means
        # the wait is inside the collective. `--probe-sync` (below) is what separates those two when
        # the markers alone are ambiguous.
        n_timed = min(8, total_chunks)
        for i in range(total_chunks):
            off = i * chunk
            n = min(chunk, total - off)
            k = i & 1
            timed = i < n_timed or i == total_chunks - 1
            if role == "push":
                # Wait for this chunk's bytes, then stage and broadcast it, both on the default stream.
                # `ready[i]` is set by the reader thread the moment chunk i's last byte is in `host`;
                # the wait resolves in host memory and never touches CUDA. The copy_ is non_blocking so
                # it is only *enqueued* here -- the caller does not wait for the transfer, which keeps
                # this path free of any host wait between the first collective and the ack. Chunk
                # ranges in `host` do not alias, so the reader can be filling chunk i+1 while the
                # stream is still reading chunk i.
                tw = time.perf_counter()
                ready[i].wait()
                t_wait = time.perf_counter() - tw
                if read_err:
                    raise read_err[0]
                # First iteration only: `t_load` is the pre-loop timestamp, so reporting it again on
                # a later iteration would print a number that describes nothing.
                if i == 0 and it == 0:
                    stage("first_chunk_read", cid=ids[0][:12], MiB=round(n / MiB, 1),
                          s=round(time.time() - t_load, 3))
                payload[off:off + n].copy_(host[off:off + n], non_blocking=True)
                dist.broadcast(payload[off:off + n], src=0, group=pg)
                # Only the first chunk's wait is expected to be long; a large t_wait later means the
                # disk has fallen behind the fabric and the read, not the wire, is the limit.
                if i < n_timed or i == total_chunks - 1:
                    stage("chunk_t", i=i, wait_s=round(t_wait, 6), n=n)
            else:
                # Broadcast STRAIGHT into the final layout. The receiver used to land each chunk in
                # a two-buffer staging pair and then copy it D2D into `payload`; that corrupts the
                # model (19 of 21 chunks failed their sha256 on a --persist run) because the D2D out
                # of bufs[k] is asynchronous and the broadcast of chunk i+2 writes the same bufs[k]
                # before that copy has retired. Two buffers cannot cover an async copy plus a
                # collective. Receiving in place removes the staging pair and the D2D entirely, and
                # makes both ranks symmetric: each side touches exactly payload[off:off+n].
                tb = time.perf_counter()
                dist.broadcast(payload[off:off + n], src=0, group=pg)
                t_bcast = time.perf_counter() - tb
                # --probe-sync, the decisive experiment. Fast here -> the collective really finished and
                # the D2D copy behind it is the ~165 ms; ~165 ms here -> the collective is being dragged
                # out and the copy is innocent. Strictly pull-only, twice over: it sits in the receiver
                # branch AND re-checks the role. A host sync on the sending side while a collective is
                # live deadlocks this driver, so this must never be reachable from the push rank.
                if a.probe_sync and role == "pull" and i < 3:
                    ts = time.perf_counter()
                    torch.cuda.synchronize()
                    stage("probe_sync", i=i, sync_s=round(time.perf_counter() - ts, 6))
                if timed:
                    # `n` here is bytes; the existing "chunk" marker reports MiB.
                    stage("chunk_t", i=i, bcast_s=round(t_bcast, 6), n=n)
            if i == 0 and it == 0:
                # First iteration only, same reason as first_chunk_read above.
                stage("first_chunk_done", cid=ids[0][:12], MiB=round(n / MiB, 1))
            stage("chunk", i=i, n=round(n / MiB, 1), role=role)

        if role == "push":
            # No host wait on this side. The receiver's ack is the completion signal, and it only
            # arrives once the receiver has synced and landed every byte in its store.
            #
            # One accept per iteration, mirroring the receiver's one connect per iteration; the
            # listener is closed once after the loop, never here. A rank given a different
            # --repeat than its peer hangs on this accept, waiting for an ack that never comes.
            issue = time.time() - t0
            srv.settimeout(600)
            conn, _ = srv.accept()
            with conn:
                conn.recv(4096)
            dt_it = time.time() - t0
            if last:
                stage("loop_done", issue_s=round(issue, 3), ack_s=round(dt_it - issue, 3))
        else:
            # The four markers below pin down where a future hang sits, so do not reorder them.
            # They describe one transfer, so they are emitted on the LAST iteration only; printing
            # them N times would bury the set that matches `seconds`. `iter` carries every pass.
            if last:
                stage("loop_issued", chunks=total_chunks)
                # Deliberately between the loop and the device sync: it brackets the last enqueue,
                # so a gap between loop_issued and this stamp is host time spent after the loop,
                # and the gap from here to "synced" is the GPU draining what the loop enqueued.
                stage("post_loop_pre_sync", chunks=total_chunks)
            torch.cuda.synchronize()
            if last:
                stage("synced")
            if a.persist:
                # ONE synchronous pageable device->host copy, then the bytes land in the chunk store.
                # No pinned buffer on this side: the persist path is allowed to be slower than the
                # transport, because it is not on the path to the weights being usable. Under
                # --repeat this runs every iteration and rewrites the same files; that is accepted
                # rather than special-cased -- and it is why --persist is not the steady-state case.
                host_view = payload.cpu().numpy()
                if last:
                    stage("host_copied", MiB=round(total / MiB, 1))
                off = 0
                for cid in ids:
                    n = sizes[cid]
                    p = path_of(cid)
                    os.makedirs(os.path.dirname(p), exist_ok=True)
                    with open(p, "wb", buffering=0) as fh:
                        fh.write(host_view[off:off + n])
                    off += n
            else:
                # Nothing is copied and nothing is written: `payload` already holds every byte in HBM
                # and is what the caller serves from. No host pointer is touched at all.
                if last:
                    stage("device_resident", MiB=round(total / MiB, 1))
            dt_it = time.time() - t0
            if last:
                stage("loop_done", s=round(dt_it, 3))
            # One connect per iteration, in step with the sender's one accept per iteration. It is
            # still sent after this iteration's timing was taken, so the receiver's `s` remains
            # "time until the weights are usable" and does not include the handshake.
            sock = socket.create_connection((a.peer_ip, a.ack_port), timeout=60)
            with sock:
                sock.sendall(b"done")
        it_s.append(dt_it)
        stage("iter", i=it, s=round(dt_it, 3),
              GBps=round(total / dt_it / 1e9, 2) if dt_it else None)

    if role == "push":
        # One listener for the whole run; it is not a per-transfer resource. Closing it inside the
        # loop would leave the next iteration's accept() with nothing to listen on.
        srv.close()
    # `seconds`/`GBps` describe the last iteration; these two separate the setup cost from it.
    # `steady_s` is None at N == 1 by design (there is no steady-state sample to average).
    dt = it_s[-1]
    first_s = it_s[0]
    # Steady state is iterations 1..N-1: no cold pin, no cold read, no cold landing pages.
    steady_s = round(sum(it_s[1:]) / len(it_s[1:]), 3) if len(it_s) > 1 else None

    print(json.dumps({"event": role, "chunks": len(ids), "MiB": round(total / MiB, 1),
                      # `seconds`/`GBps` describe the LAST iteration, so a --repeat 1 run reads
                      # exactly as it always did. `first_s` is iteration 0 (cold pinned pool, cold
                      # reads, cold landing pages); `steady_s` is the mean of iterations 1..N-1 and
                      # is null at N == 1. The gap between first_s and steady_s is the one-time
                      # setup cost that a fresh process per restore was charging to the transfer.
                      "seconds": round(dt, 3), "persist": bool(a.persist),
                      "repeat": a.repeat,
                      "first_s": round(first_s, 3),
                      "steady_s": steady_s,
                      # Sender only: total time the reader thread spent on disk, from the start of
                      # the first read to the last chunk landing. Compared against `seconds` it says
                      # whether the reads stayed behind the broadcasts or became the limit. Null
                      # under --source-pool, where no reader thread is ever started.
                      "read_s": round(read_finish[0] - t_load, 3)
                                if role == "push" and read_finish else None,
                      "GBps": round(total / dt / 1e9, 2) if dt else None}), flush=True)


# ---------------------------------------------------------------- verify
def cmd_verify(a):
    sys.path.insert(0, os.path.join(REPO, "main_lib"))
    from fluidckpt.live.chunks import ChunkedModel, DirChunkStore, load_manifest

    m = load_manifest(os.path.join(a.manifest_dir, "manifest.json"))
    store = DirChunkStore(a.store)
    missing = [c.id for c in m.chunks if not store.has(c.id)]
    if missing:
        raise SystemExit(f"{len(missing)} chunks missing, e.g. {missing[0][:12]}")
    model = ChunkedModel(m, lambda cid: store)
    out = a.out
    os.makedirs(out, exist_ok=True)
    ok = model.verify()
    model.materialize_dir(out)
    # materialize_dir already checks each file's sha256 against the manifest
    print(json.dumps({"event": "verified", "files": len(m.files), "chunks": len(m.chunks),
                      "bytes_MiB": round(sum(f["size"] for f in m.files.values()) / MiB, 1),
                      "all_hashes_match": bool(ok), "out": out}), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["place", "seed", "fetchplan", "push", "pull", "verify"])
    ap.add_argument("--model-dir")
    ap.add_argument("--out", default=".")
    ap.add_argument("--nodes", default="node27,node28,node29,node30")
    ap.add_argument("--free", help="node=bytes,node=bytes (usable space per node)")
    ap.add_argument("--fill", type=float, default=0.5)
    ap.add_argument("--replicas", type=int, default=1)
    ap.add_argument("--policy", choices=["balanced", "free"], default="balanced")
    ap.add_argument("--chunk-mib", type=int, default=64)
    ap.add_argument("--store")
    ap.add_argument("--have", default="", help="comma list of chunk ids already local")
    ap.add_argument("--chunks", help="json list of chunk ids this pair moves")
    ap.add_argument("--role", choices=["push", "pull"])
    ap.add_argument("--master")
    ap.add_argument("--ack-port", type=int, default=45000)
    ap.add_argument("--peer-ip", default="12.11.3.27")
    ap.add_argument("--gpu", type=int, default=0)
    # Off by default: the MaaS requirement is that the weights are resident and servable in HBM,
    # and writing them to the chunk store as well is an optional action off the critical path.
    ap.add_argument("--persist", action="store_true")
    # Diagnostic only, default OFF: on the pull rank, host-sync right after the broadcast and
    # before the D2D copy, for the first three chunks, to decide whether the slow chunks are inside
    # the collective or in the copy behind it. It cannot run on the sender -- a host sync with a
    # live collective on that side deadlocks this driver -- and the code guards that explicitly.
    ap.add_argument("--probe-sync", action="store_true",
                    help="pull rank only: time torch.cuda.synchronize() after each of the first "
                         "3 broadcasts (diagnostic, adds host stalls to the measurement)")
    # Diagnostic only, default OFF: on the pull rank, physically back every page of `payload`
    # (zero_ + one device-wide sync) BEFORE the timed region, so the first-touch cost of the
    # 1.2 GiB landing allocation is not charged to the first collective and D2D of the loop.
    # Pull only: on the sender `payload` is the broadcast source, filled per chunk from the reader.
    ap.add_argument("--prefault", action="store_true",
                    help="pull rank only: zero_+sync the landing payload before t0 so first-touch "
                         "page population is outside the timed region (diagnostic)")
    ap.add_argument("--source-pool", action="store_true",
                    help="sender treats its pinned pool as already holding the model; "
                         "no chunk file is read (resident keep-alive source)")
    # Accepted but unused: the pull path always waits device-wide on the host.
    # The flag stays so fetch_serial.py's launch line keeps working unchanged.
    ap.add_argument("--wait-mode", choices=["device", "stream"], default="device",
                    help=argparse.SUPPRESS)
    ap.add_argument("--manifest-dir")
    ap.add_argument("--dst-node", default="node27")
    # Repeat the whole transfer N times in ONE process and report the steady-state iterations
    # separately. The first iteration pays the cold pin (0.5-0.64 s for 1.2 GiB), the cold read and
    # the cold landing pages; iterations 1..N-1 pay none of them and are what the keep-alive
    # design actually promises. Both ranks must be given the same value -- a mismatch desynchronises
    # the collectives and hangs.
    ap.add_argument("--repeat", type=int, default=1,
                    help="run the timed transfer N times in this process; iteration 0 is cold, "
                         "1..N-1 are steady state (must be the same on both ranks)")
    a = ap.parse_args()
    {"place": cmd_place, "seed": cmd_seed, "fetchplan": cmd_fetchplan,
     "push": cmd_move, "pull": cmd_move, "verify": cmd_verify}[a.cmd](a)


if __name__ == "__main__":
    main()
