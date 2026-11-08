"""Cluster worker: prepare every participant before entering ordered pair collectives."""
import argparse
import datetime
import hashlib
import json
import os
import resource
import threading
import time
import traceback
import uuid
from contextlib import nullcontext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import torch
from fluidckpt.cluster.catalog import Catalog
from fluidckpt.cluster.planning import validate_plan, restore_plan, placement_plan
from fluidckpt.memory import HostAllocation
from fluidckpt.serving.keeper import Worker, HbmTarget
from fluidckpt.serving.replicas import identity
from fluidckpt.serving.measure import emit_to
from fluidckpt.executor.pipeline import DoubleBufferCopier
from fluidckpt.executor.chunks import execution_chunk, STAGING_BYTES

CHUNK = 32 << 20


class ClusterWorker(Worker):
    def record_targets(self, run, event):
        for target in run["plan"]["targets"]:
            if target["worker"] == self.rank and target["mode"] == "live":
                emit_to(target.get("metrics_directory"), target["tp"], event,
                        role="keeper", worker=self.rank)

    def __init__(self, args):
        super().__init__(args)
        torch.set_num_threads(1)
        self.catalog = Catalog({"dram": args.dram_budget, "hbm": args.hbm_budget,
                                "ssd": getattr(args, 'ssd_budget', 128 << 30)})
        self.service_reserve = None
        self.runs = {}
        self.epoch = f"{args.session}/{args.node}/{args.gpu}/{os.getpid()}/{uuid.uuid4().hex}"

    def init_mesh(self):
        self.members = []
        for part in self.a.mesh.split(","):
            node, span = part.split(":")
            lo, hi = map(int, span.split("-"))
            self.members.extend((node, gpu) for gpu in range(lo, hi+1))
        self.rank = self.members.index((self.node, self.gpu))
        torch.cuda.set_device(self.gpu)
        torch.distributed.init_process_group("nccl", init_method=f"tcp://{self.a.master}",
            rank=self.rank, world_size=len(self.members), timeout=datetime.timedelta(seconds=180))
        self.pair_of = {}
        for i in range(len(self.members)):
            for j in range(i+1, len(self.members)):
                group = torch.distributed.new_group([i, j], timeout=datetime.timedelta(seconds=180))
                self.pair_of[i, j] = self.pair_of[j, i] = group
        self.staging = torch.empty(STAGING_BYTES, dtype=torch.uint8, device=self.dev)
        self.pipeline_staging = [self.staging[:CHUNK], self.staging[CHUNK:]]
        self.dist_ready = True

    def inventory(self, compact=False):
        from fluidckpt.topology.discover import memory_node
        mem = memory_node(self.a.numa)
        free, total = torch.cuda.mem_get_info(self.dev)
        return {"worker": self.rank, "node": self.node, "gpu": self.gpu, "numa": self.a.numa,
                "epoch": self.epoch, "session": self.a.session, "pid": os.getpid(),
                "capabilities": ["local_dram", "local_hbm", "local_ssd", "peer", "network"],
                "budget_bytes": self.catalog.budgets, "used_bytes": self.catalog.used(),
                "replicas": self.catalog.inventory(compact=compact), "transactions": list(self.runs),
                "transaction_phases": {tx: r["phase"] for tx, r in self.runs.items()},
                "staging_bytes": 2*CHUNK, "hbm_allocated_bytes": torch.cuda.memory_allocated(self.dev),
                "hbm_reserved_bytes": torch.cuda.memory_reserved(self.dev),
                "headroom_bytes": {"hbm": free, "dram": max(0, mem.get("MemFree", 0)+mem.get("FilePages", 0)-mem.get("Shmem", 0))},
                "hbm_total_bytes": total, "headroom_sample_ns": time.monotonic_ns()}

    def adopt(self, body):
        key = body["snap"], body["tp"]
        sh = self.shards[key]
        desc = {"id": body["id"], "identity": identity(sh.meta, sh.index), "tp": body["tp"],
                "off": 0, "n": sh.nbytes, "tier": "dram", "worker": self.rank,
                "node": self.node, "gpu": self.gpu, "numa": sh.owner.numa,
                "meta": sh.meta, "index": sh.index, "epoch": self.epoch}
        self.catalog.reserve(desc, lambda: (sh.buf, sh.owner))
        self.catalog.publish(desc["id"], True)
        self.shards.pop(key)
        return next(r for r in self.catalog.inventory() if r["id"] == desc["id"])

    def allocate(self, desc):
        torch.cuda.set_device(self.gpu)
        if desc['tier'] == 'ssd':
            from fluidckpt.memory.ssd import RemoteExtent
            return None, RemoteExtent(desc['store_endpoint'], desc['id'], desc['n'])
        if desc["tier"] == "dram":
            socket_path = os.environ.get('FLUIDCKPT_CPU_OWNER_SOCKET')
            if socket_path:
                from fluidckpt.memory.owned_host import OwnedHostAllocation, register_manifest
                register_manifest(socket_path, desc)
                owner = OwnedHostAllocation(socket_path, desc['id'], desc['n'],
                    desc.get('numa', self.a.numa),
                    {key: value for key, value in desc.items() if key not in ('meta', 'index')})
            else:
                owner = HostAllocation(desc["n"], "numa_registered", desc.get('numa', self.a.numa))
            return owner.tensor, owner
        free, _ = torch.cuda.mem_get_info(self.dev)
        if desc["n"] + (2 << 30) > free:
            raise MemoryError("insufficient free GPU memory")
        return torch.empty(desc["n"], dtype=torch.uint8, device=self.dev), None

    def adopt_owned(self, body):
        from fluidckpt.memory.owner import call
        from fluidckpt.memory.owned_host import OwnedHostAllocation
        path = os.environ['FLUIDCKPT_CPU_OWNER_SOCKET']
        inventory, _ = call(path, 'inventory')
        selected = set(body['keys']) if 'keys' in body else None
        restored = []
        for region in inventory['regions']:
            desc = dict(region['metadata'])
            if region['state'] != 'VALID': continue
            if selected is not None:
                if region['key'] not in selected: continue
            elif desc.get('worker') != self.rank:
                continue
            manifest, _ = call(path, 'get_manifest', key=desc['identity'])
            desc.update(manifest, worker=self.rank, node=self.node, gpu=self.gpu, epoch=self.epoch)
            owner = OwnedHostAllocation(path, region['key'])
            self.catalog.reserve(desc, lambda: (owner.tensor, owner))
            self.catalog.publish(desc['id'], True)
            restored.append(desc['id'])
        return {'adopted': restored}

    def prepare(self, body):
        tx, plan = body["tx"], body["plan"]
        if tx in self.runs: raise ValueError("duplicate transaction")
        if body["epochs"].get(str(self.rank)) != self.epoch:
            raise ValueError("worker restarted or plan belongs to a different session")
        validate_plan(plan)
        execution_chunk(plan)
        if plan.get("executor", "serial") not in ("serial", "double_buffer"):
            raise ValueError("unsupported executor")
        if "fail_after_chunks" in plan and (type(plan["fail_after_chunks"]) is not int or plan["fail_after_chunks"] < 1):
            raise ValueError("invalid failure injection bound")
        mine = [o for o in plan["ops"] if o["src_w"] == self.rank]
        if any(o.get("source_epoch") != self.epoch for o in mine):
            raise ValueError("source descriptor belongs to an old worker process")
        self.catalog.acquire(tx, mine)
        run = {"plan": plan, "targets": {}, "phase": "PREPARED", "built": [], "ops": []}
        self.runs[tx] = run
        self.record_targets(run, "target_accepted")
        directory = os.environ.get('FLUIDCKPT_PREPARE_PROFILE_DIR')
        trace = None
        if directory and any(t['mode'] == 'live' and t['worker'] == self.rank for t in plan['targets']):
            from fluidckpt.serving.prepare_trace import PrepareTrace
            trace = PrepareTrace(directory, self.rank, tx)
        span = trace.span if trace else lambda *a, **kw: nullcontext()
        try:
            for target in plan["targets"]:
                if target["worker"] != self.rank: continue
                with span('identity_hash'):
                    if identity(target["meta"], target["index"]) != target["identity"]:
                        raise ValueError("target manifest identity mismatch")
                if target["mode"] == "live":
                    if target["off"] != 0 or target["n"] != max(o+n for o, n in target["index"].values()):
                        raise ValueError("live target requires the complete snapshot")
                    with span('wait_skeleton'):
                        path = self.wait_skeleton(target["tag"], target["tp"], timeout=30)
                    with span('hbm_target_total'):
                        if trace:
                            buf = HbmTarget(path, target['index'], self.gpu, target['meta'], trace=trace)
                        else:
                            buf = HbmTarget(path, target['index'], self.gpu, target['meta'])
                elif target["mode"] == "replica":
                    desc = {k: target[k] for k in ("id", "identity", "tp", "off", "n", "tier", "meta", "index")}
                    if target['tier'] == 'ssd':
                        desc.update(store_endpoint=target['store_endpoint'], storage_node=target['storage_node'])
                    desc.update(worker=self.rank, node=self.node, gpu=self.gpu,
                                numa=target.get('numa', self.a.numa), epoch=self.epoch)
                    buf = self.catalog.reserve(desc, lambda: self.allocate(desc))
                    run["built"].append(desc["id"])
                else:
                    raise ValueError("unknown target mode")
                run["targets"][target["key"]] = buf
            self.record_targets(run, "target_mapped")
            return {"worker": self.rank, "prepared": True}
        except BaseException:
            self.abort({"tx": tx})
            raise
        finally:
            if trace: trace.finish()

    def land(self, target, off, src):
        if isinstance(target, HbmTarget):
            if target.scatter(off, src) != src.numel():
                raise RuntimeError("incomplete live target write")
        else:
            a = off-target["off"]
            if target['tier'] == 'ssd':
                target['owner'].write(a, src)
            else:
                target["tensor"][a:a+src.numel()].copy_(src, non_blocking=True)

    def execute(self, body):
        directory = os.environ.get("FLUIDCKPT_EXECUTE_PROFILE_DIR")
        plan = self.runs[body["tx"]]["plan"]
        self.execution_profile = None
        if directory and (plan.get("profile_execute", False) or any(t["mode"] == "live" for t in plan["targets"])):
            from fluidckpt.executor.profiling import ExecuteProfile
            try:
                with ExecuteProfile(directory, self.rank, body["tx"]) as profile:
                    self.execution_profile = profile
                    return self._execute(body)
            finally:
                self.execution_profile = None
        return self._execute(body)

    def _execute(self, body):
        torch.cuda.set_device(self.gpu)
        run = self.runs[body["tx"]]
        if run["phase"] != "PREPARED": raise ValueError("transaction is not prepared")
        started = time.monotonic()
        run["phase"] = "EXECUTING"
        chunk = execution_chunk(run['plan'])
        self.record_targets(run, "target_execute_begin")
        records = []
        pipeline = None
        try:
            if run["plan"].get("executor", "serial") == "double_buffer":
                pipeline = DoubleBufferCopier(self.dev, self.pipeline_staging,
                    lambda buf, op: torch.distributed.broadcast(buf, src=op["src_w"], group=self.pair_of[op["src_w"], op["dst_w"]]),
                    self.land, trace=run["plan"].get("trace_pipeline", False))
            # Every pair sees the same operation order. Disjoint pairs can advance independently.
            for op in run["plan"]["ops"]:
                if self.rank not in (op["src_w"], op["dst_w"]): continue
                sender, receiver = self.rank == op["src_w"], self.rank == op["dst_w"]
                source = self.catalog.entries[op["replica_id"]] if sender else None
                target = run["targets"][op["target"]] if receiver else None
                stamp = time.monotonic()
                self.log(ev="cluster_op_begin", tx=body["tx"], op_id=op["op_id"],
                         src=op["src_w"], dst=op["dst_w"], n=op["n"], kind=op["kind"])
                details = {}
                if self.execution_profile: self.execution_profile.begin_op(op)
                if pipeline:
                    details = pipeline.move(op, source["tensor"] if sender else None, target, sender, receiver,
                                            run["plan"].get("fail_after_chunks"))
                for pos in ([] if pipeline else range(0, op["n"], chunk)):
                    n = min(chunk, op["n"]-pos)
                    if sender:
                        a = op["source_offset"]+pos
                        view = (source['owner'].read(a, n) if source['tier'] == 'ssd'
                                else source["tensor"][a:a+n])
                    if sender and receiver:
                        self.land(target, op["off"]+pos, view)
                    else:
                        buf = self.staging[:n]
                        if sender: buf.copy_(view, non_blocking=True)
                        torch.distributed.broadcast(buf, src=op["src_w"], group=self.pair_of[op["src_w"], op["dst_w"]])
                        if receiver: self.land(target, op["off"]+pos, buf)
                    # A single bounded staging buffer may be reused only after the land/copy finishes.
                    torch.cuda.synchronize(self.dev)
                if self.execution_profile: self.execution_profile.end_op()
                records.append({"op_id": op["op_id"], "role": "both" if sender and receiver else "source" if sender else "target",
                                "replica_id": op["replica_id"], "bytes": op["n"], "kind": op["kind"],
                                "elapsed_s": time.monotonic()-stamp, "pipeline": details})
                self.log(ev="cluster_op_end", tx=body["tx"], op_id=op["op_id"], elapsed_s=time.monotonic()-stamp)
            run.update(phase="FILLED", records=records)
            self.record_targets(run, "target_copy_complete")
            return {"worker": self.rank, "records": records, "execute_s": time.monotonic()-started,
                    "executor": run["plan"].get("executor", "serial"), "staging_bytes": STAGING_BYTES,
                    "chunk_bytes": chunk}
        except BaseException:
            run["phase"] = "FAILED"
            raise
        finally:
            if pipeline: pipeline.synchronize()

    def digest(self, tensor, off, n):
        result = hashlib.sha256()
        for pos in range(0, n, CHUNK):
            result.update(tensor[off+pos:off+min(n, pos+CHUNK)].cpu().numpy().tobytes())
        return result.hexdigest()

    def target_digest(self, target, off, n):
        if not isinstance(target, HbmTarget):
            if target['tier'] == 'ssd':
                return self.extent_digest(target, off-target['off'], n)
            return self.digest(target["tensor"], off-target["off"], n)
        result = hashlib.sha256()
        checked = 0
        for start, length, tensor in target.segs:
            a, b = max(off, start), min(off+n, start+length)
            for pos in range(a, b, CHUNK):
                count = min(CHUNK, b-pos)
                result.update(tensor[pos-start:pos-start+count].cpu().numpy().tobytes())
                checked += count
        if checked != n: raise RuntimeError("incomplete digest coverage")
        return result.hexdigest()

    def extent_digest(self, source, off, n):
        if source['tier'] != 'ssd': return self.digest(source['tensor'], off, n)
        result = hashlib.sha256()
        for pos in range(0, n, CHUNK):
            result.update(source['owner'].read(off+pos, min(CHUNK, n-pos)).numpy().tobytes())
        return result.hexdigest()

    def verify_run(self, body):
        torch.cuda.set_device(self.gpu)
        run = self.runs[body["tx"]]
        if run["phase"] not in ("FILLED", "READY", "VERIFIED"):
            raise ValueError("verify requires completed transfers")
        rows = []
        for op in run["plan"]["ops"]:
            row = {"op_id": op["op_id"], "n": op["n"]}
            if op["src_w"] == self.rank:
                source = self.catalog.entries[op["replica_id"]]
                row["source_sha256"] = self.extent_digest(source, op["source_offset"], op["n"])
            if op["dst_w"] == self.rank:
                row["target_sha256"] = self.target_digest(run["targets"][op["target"]], op["off"], op["n"])
            if len(row) > 2: rows.append(row)
        run["verified_rows"] = rows
        return {"worker": self.rank, "rows": rows}

    def complete(self, body):
        run = self.runs[body["tx"]]
        if run["phase"] != "FILLED": raise ValueError("transfer not complete")
        # Live readiness follows successful, synchronized transfer. Full hashing is performed outside its load timer.
        if run["built"]:
            if "verified_rows" not in run or not body.get("verified"):
                raise ValueError("new replicas require complete content verification")
            for rid in run["built"]:
                entry = self.catalog.entries[rid]
                if entry['tier'] == 'ssd': entry['owner'].commit()
                self.catalog.publish(rid, True)
        self.record_targets(run, "target_ready_publish")
        for target in run["targets"].values():
            if isinstance(target, HbmTarget): target.done({"cluster": True, "worker": self.rank, "tx": body["tx"]})
        run["phase"] = "READY"
        return {"worker": self.rank, "ready": True}

    def finish(self, body):
        run = self.runs.get(body["tx"])
        if run and run["phase"] != "READY": raise ValueError("cannot finish incomplete transaction")
        self.runs.pop(body["tx"], None)
        self.catalog.release(body["tx"])
        torch.cuda.empty_cache()
        return {"finished": body["tx"]}

    def abort(self, body):
        torch.cuda.set_device(self.gpu)
        torch.cuda.synchronize()
        run = self.runs.pop(body["tx"], None)
        if run:
            run["targets"].clear()
            for rid in run["built"]:
                if self.catalog.entries[rid]["state"] != "VALID": self.catalog.retire(rid)
        self.catalog.release(body["tx"])
        torch.cuda.empty_cache()
        return {"aborted": body["tx"]}

    def retire(self, body):
        torch.cuda.set_device(self.gpu)
        self.catalog.retire(body["id"])
        torch.cuda.ipc_collect()
        torch.cuda.empty_cache()
        return self.inventory(compact=True)

    def reserve_service_memory(self, body):
        self.service_reserve = None
        torch.cuda.empty_cache()
        if body['nbytes']:
            self.service_reserve = torch.zeros(body['nbytes'], dtype=torch.uint8, device=self.dev)
        torch.cuda.synchronize()
        return {'reserved_bytes': body['nbytes'], 'free_bytes': torch.cuda.mem_get_info(self.dev)[0]}

    def export_ipc(self, body):
        import pickle
        from pathlib import Path
        entry = self.catalog.entries[body['id']]
        if entry['tier'] != 'hbm' or entry['off'] != 0:
            raise ValueError('IPC export requires a complete HBM replica')
        handle = entry['tensor'].untyped_storage()._share_cuda_()
        entries = [{'name': name, 'nbytes': n, 'handle': handle, 'packed_offset_bytes': off}
                   for name, (off, n) in entry['index'].items()]
        root = Path(body['directory']); root.mkdir(parents=True, exist_ok=True)
        path = root / f'rank{entry["tp"]}.pkl'
        with path.open('wb') as stream: pickle.dump({'meta': entry['meta'], 'entries': entries}, stream)
        return {'path': str(path), 'nbytes': entry['n']}


def main(args):
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    worker = ClusterWorker(args)
    class Handler(BaseHTTPRequestHandler):
        def reply(self, result, status=200):
            data = json.dumps(result).encode()
            self.send_response(status); self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(data)
        def do_GET(self):
            if self.path in ("/inventory", "/inventory?compact=1"):
                self.reply(worker.inventory(compact=self.path.endswith("?compact=1")))
            else:
                self.reply(worker.status())
        def do_POST(self):
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                op = self.path.strip("/")
                with worker.lock:
                    torch.cuda.set_device(worker.gpu)
                    if op == "mesh": worker.init_mesh(); result = worker.inventory()
                    elif op == "capture": result = worker.capture(body["tag"], body["snap"], timeout=60, retain_live=True)
                    elif op == "synth":
                        tp_size = body.get("tp_size", 2)
                        if type(tp_size) is not int or tp_size <= 0 or type(body["tp"]) is not int or not 0 <= body["tp"] < tp_size:
                            raise ValueError("synthetic TP size and rank must be positive/range-valid integers")
                        result = worker.synth(body["snap"], body["tp"], body["nbytes"], body.get("seed", 0))
                        worker.shards[body["snap"], body["tp"]].meta.update(
                            model_identity=f"fixture:{body.get('seed', 0)}", tp_size=tp_size)
                        result.update(synthetic=True, tp_size=tp_size, tp_rank=body["tp"])
                    elif op == "verify_capture": result = worker.verify(body["tag"], body["snap"], body["tp"], "capture")
                    elif op == "release_live": result = worker.release_live(body["tag"])
                    elif op == "warm": result = worker.warm(body["pairs"])
                    elif op == "plan_restore": result = restore_plan(body["replicas"], body["targets"], body["workers"], body["profile"], body["strategy"])
                    elif op == "plan_joint":
                        from fluidckpt.cluster.managed import solve_joint_request
                        result = solve_joint_request(body["workers"], body["request"], body["profile"])
                    elif op == 'plan_eviction':
                        from fluidckpt.cluster.eviction import plan_eviction
                        result = plan_eviction(**body)
                    elif op == 'capture_regions':
                        from fluidckpt.cluster.capture import capture_regions
                        result = capture_regions(worker, **body)
                    elif op == "plan_placement": result = placement_plan(body["replicas"], body["requests"], body["workers"], body["profile"], body["policy"], body["scenarios"], body["extra_budget"])
                    elif op in ("adopt", "adopt_owned", "prepare", "execute", "verify_run", "complete", "finish", "abort", "retire",
                                "reserve_service_memory", "export_ipc"):
                        result = getattr(worker, op)(body)
                    else: raise ValueError("unknown operation")
                self.reply(result)
            except Exception as exc:
                traceback.print_exc()
                self.reply({"error": repr(exc), "traceback": traceback.format_exc()}, 500)
        def log_message(self, *args): pass
    print(json.dumps({"ready": True, "pid": os.getpid(), "node": args.node, "gpu": args.gpu}), flush=True)
    ThreadingHTTPServer(("0.0.0.0", args.port_base+args.gpu), Handler).serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--node", required=True); parser.add_argument("--gpu", type=int, required=True)
    parser.add_argument("--port-base", type=int, required=True); parser.add_argument("--mesh", required=True)
    parser.add_argument("--master", required=True); parser.add_argument("--handoff", required=True)
    parser.add_argument("--session", required=True); parser.add_argument("--numa", type=int, required=True)
    parser.add_argument("--dram-budget", type=int, default=48 << 30)
    parser.add_argument("--hbm-budget", type=int, default=40 << 30)
    parser.set_defaults(memory_backend="numa_registered", replica_budget_bytes=8 << 30)
    main(parser.parse_args())
