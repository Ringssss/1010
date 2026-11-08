"""Restore one fresh engine from retained DRAM/SSD backing and leave it parked.

This driver owns only its new engine process specs. It never starts/stops keepers,
CPU owners, containers, or engines named in the input session. Its bootstrap
measurement includes SHA before readiness and is not a performance comparison.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import resource
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT / "codex_coding/src/task2/cluster"))
sys.path.insert(0, str(ROOT / "main_lib"))
from run_system import NODES, SystemRun
from run_lifecycle import LifecycleRun
from fluidckpt.cluster.control import Cluster, request
from fluidckpt.serving.measure import summarize

RESULT_ROOT = ROOT / "codex_coding/results/v0.1.23.3/task2/paper_e1_e8"


def absolute(path):
    path = Path(path)
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    temp.replace(path)


def set_arg(command, name, value):
    if name in command:
        command[command.index(name) + 1] = str(value)
    else:
        command.extend([name, str(value)])


class RestoreEngine(SystemRun):
    generate_stream = LifecycleRun.generate_stream

    def __init__(self, args):
        self.args = args
        self.input_session = absolute(args.resume_session or args.session)
        self.state = json.loads(self.input_session.read_text())
        self.root_session = absolute(self.state["root_session"])
        self.root = json.loads(self.root_session.read_text())
        self.root_out = self.root_session.parent
        self.model = copy.deepcopy(self.state["models"][args.model])
        self.tp = int(self.model["tp"])
        if self.tp != (2 if args.model == "B" else 16):
            raise ValueError("expected B=Qwen TP2 or A=GLM TP16")
        if args.model == "A" and args.node != "node27":
            raise ValueError("GLM TP16 uses node27 rank0 and node29 rank1")
        self.out = (RESULT_ROOT / args.run).resolve()
        if RESULT_ROOT.resolve() not in self.out.parents:
            raise ValueError("run must be beneath the versioned result root")
        self.out.mkdir(parents=True, exist_ok=False)
        os.chmod(self.out, 0o777)
        self.processes, self.owned_containers = [], []
        NODES.clear()
        NODES.update(self.root["nodes"])
        self.env = self.root["env"].copy()
        self.members = [tuple(member) for member in self.root["members"]]
        self.endpoints = self.root["endpoints"]
        self.handoff = self.root_out / "handoff"
        self.profile = copy.deepcopy(self.state["profile"])
        self.cluster = Cluster(self.endpoints, self.profile, planner_endpoint=self.endpoints[0])
        self.phase = "paper_fresh_" + hashlib.sha256(str(self.out).encode()).hexdigest()[:18]
        if (self.handoff / self.phase).exists():
            raise ValueError("handoff tag already exists")
        self.manifest = absolute(args.manifest) if args.manifest else self.handoff / (
            "life_B_capture_reclaim02" if args.model == "B" else "capture") / "derived.json"
        self.command_path = self.out / "engine.command.json"
        self.metrics = self.out / "metrics" / self.phase
        self.slot_name = args.slot_name or args.model
        workers = [self.members.index((args.node, gpu)) for gpu in [0, 1]] if args.model == "B" else list(range(16))
        self.slot = {"model": args.model, "label": "fresh_" + self.slot_name,
                     "endpoint": f"http://{NODES[args.node]['ip']}:{args.port}",
                     "workers": workers, "command": str(self.command_path),
                     "parked": False, "handoff": str(self.handoff),
                     "manifest": str(self.manifest), "metrics": str(self.metrics),
                     "ownership_run": str(self.out)}
        sources = list((ROOT / "main_lib/fluidckpt").rglob("*.py"))
        sources += list((ROOT / "codex_coding/src/task2/cluster").glob("*.py"))
        sources += [Path(__file__).resolve(), ROOT / "codex_coding/src/serving/sglang_fluid_launch.py"]
        self.result = {"development": "v0.1.23.3", "task": "task2", "status": "running",
                       "events": [], "model": args.model, "slot": self.slot,
                       "input_session": str(self.input_session), "root_session": str(self.root_session),
                       "handoff": str(self.handoff), "phase": self.phase,
                       "scope": "Fresh dummy-loader bootstrap from retained DRAM/SSD backing; full SHA before publication; not an e1-e6 performance sample",
                       "code_sha256": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
                       "input_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in [self.input_session, self.root_session]},
                       "cleanup_errors": []}
        self.save()

    def check_sources(self):
        workers, replicas = self.cluster.inventory(compact=True)
        wanted = set(self.model["source_ids"])
        if len(wanted) != len(self.model["source_ids"]):
            raise ValueError("duplicate source IDs in the input model")
        valid = [r for r in replicas if r["id"] in wanted]
        actual = {r["id"] for r in valid}
        if actual != wanted or len(valid) != len(wanted):
            raise RuntimeError(f"retained source set incomplete or duplicated: missing={sorted(wanted-actual)}")
        if any(r["state"] != "VALID" or r["tier"] not in {"dram", "ssd"} for r in valid):
            raise RuntimeError("retained backing requires every original source to be VALID DRAM or SSD")
        tier_bytes = {tier: sum(r["n"] for r in valid if r["tier"] == tier) for tier in ["dram", "ssd"]}
        rank_tier_bytes = {}
        for source in self.model["sources"]:
            covered = 0
            rank_rows = [r for r in valid if r["identity"] == source["identity"] and r["tp"] == source["tp"]]
            rank_tier_bytes[str(source["tp"])] = {
                tier: sum(r["n"] for r in rank_rows if r["tier"] == tier) for tier in ["dram", "ssd"]}
            for row in sorted(rank_rows, key=lambda r: r["off"]):
                if row["off"] > covered:
                    break
                covered = max(covered, row["off"] + row["n"])
            if covered < source["n"]:
                raise RuntimeError(f"incomplete retained DRAM/SSD coverage for rank {source['tp']}: {covered}/{source['n']}")
        self.source_ids = list(self.model["source_ids"])
        protocol = {"backing": "mixed_dram_ssd" if tier_bytes["ssd"] else "dram_only",
                    "source_policy": "Use the complete original retained source set without migration or replacement",
                    "build_origin": {"session": str(self.input_session), "root_session": str(self.root_session),
                                     "model": self.args.model, "captured_manifest": str(self.manifest)},
                    "selected_source_ids": self.source_ids, "tier_bytes": tier_bytes,
                    "rank_tier_bytes": rank_tier_bytes,
                    "verification": "Full SHA of all transferred model bytes before readiness publication"}
        write_json(self.out / "source_protocol.json", protocol)
        self.result["source_protocol"] = protocol
        self.result["source_inventory"] = {"workers": workers, "selected_source_ids": self.source_ids,
                                           "bytes": sum(r["n"] for r in valid), "tier_bytes": tier_bytes,
                                           "rank_tier_bytes": rank_tier_bytes}
        self.save()

    def commands(self):
        if self.args.model == "B":
            rows = [(self.args.node, self.root_out / "model_B_node27_reclaim02.process.json")]
        else:
            rows = [(node, self.root_out / f"model_{node}.process.json") for node in ["node29", "node27"]]
        result = []
        for node, template_path in rows:
            template = json.loads(template_path.read_text())
            command = template["command"].copy()
            set_arg(command, "--port", self.args.port)
            set_arg(command, "--load-format", "dummy")
            if self.args.model == "B":
                set_arg(command, "--nccl-port", self.args.nccl_port)
            else:
                set_arg(command, "--dist-init-addr", f"{NODES['node27']['ip']}:{self.args.nccl_port}")
            env = template["env"].copy()
            env.update(FLUIDCKPT_MODE="restore", FLUIDCKPT_TAG=self.phase,
                       FLUIDCKPT_HANDOFF=str(self.handoff), FLUIDCKPT_METRICS=str(self.metrics),
                       FLUIDCKPT_MANIFEST=str(self.manifest), FLUIDCKPT_LIFECYCLE=str(self.command_path),
                       FLUIDCKPT_TIMEOUT=str(self.args.timeout), FLUIDCKPT_CAPTURE_WAIT="0")
            result.append((node, command, env))
            self.result.setdefault("template_sha256", {})[str(template_path)] = hashlib.sha256(template_path.read_bytes()).hexdigest()
        return result

    def preflight_engine(self, commands):
        for rank in range(self.tp):
            layout = self.manifest.parent / f"layout.rank{rank}.json"
            if not layout.is_file():
                raise FileNotFoundError(layout)
            self.result.setdefault("manifest_sha256", {})[str(layout)] = hashlib.sha256(layout.read_bytes()).hexdigest()
        if not self.manifest.exists():
            raise FileNotFoundError(self.manifest)
        for node, _, _ in commands:
            probe = self.out / f"{node}_preflight.json"
            self.remote(node, ["python3", "codex_coding/src/task2/cluster/inspect_node.py", "--out", str(probe)])
            data = json.loads(probe.read_text())
            occupied = set(data.get("occupied_tcp_ports", data.get("listening_ports", [])))
            if occupied.intersection({self.args.port, self.args.nccl_port}):
                raise RuntimeError(f"{node}: a requested engine port is already occupied")
            for rank, worker in enumerate(self.slot["workers"]):
                member_node, gpu = self.members[worker]
                if member_node != node:
                    continue
                memory = data["gpu_memory"][f"card{gpu}"]
                free = (int(memory["vram Total Memory (MiB)"]) -
                        int(memory["vram Total Used Memory (MiB)"])) << 20
                guard = (10 if self.args.model == "B" else 12) << 30
                needed = int(self.model["sources"][rank]["n"]) + guard
                row = {"node": node, "gpu": gpu, "rank": rank, "free_bytes": free,
                       "weights_bytes": int(self.model["sources"][rank]["n"]),
                       "runtime_guard_bytes": guard, "needed_bytes": needed}
                self.result.setdefault("hbm_admission", []).append(row)
                self.save()
                if free < needed:
                    raise RuntimeError(f"insufficient HBM on {node}:GPU{gpu}: {free} free < {needed} needed")
        self.check_sources()
        self.save()

    def wait_exports(self):
        paths = [self.handoff / self.phase / f"rank{rank}.pkl" for rank in range(self.tp)]
        deadline = time.monotonic() + self.args.timeout
        while not all(path.exists() for path in paths):
            for _, spec in self.processes:
                log = self.out / (spec.name.removesuffix(".process.json") + ".log")
                tail = log.read_text(errors="replace")[-32768:] if log.exists() else ""
                markers = ("torch.OutOfMemoryError", "Received sigquit from a child process", "Traceback (most recent call last)")
                if any(marker in tail for marker in markers):
                    raise RuntimeError(f"engine failed before exports; inspect {log}")
            if time.monotonic() >= deadline:
                raise TimeoutError("fresh engine export deadline")
            time.sleep(0.25)

    def record_measurement(self):
        if self.tp == 2:
            self.result["measurement"] = summarize(self.metrics, self.tp)
            return
        by_host = {}
        for rank in range(self.tp):
            events = [json.loads(line) for line in (self.metrics / f"rank{rank}.jsonl").read_text().splitlines()]
            begin = [r for r in events if r["event"] == "load_begin"]
            end = [r for r in events if r["event"] == "weights_ready"]
            if len(begin) != 1 or len(end) != 1 or begin[0]["host"] != end[0]["host"]:
                raise ValueError(f"invalid boundaries for rank {rank}")
            by_host.setdefault(begin[0]["host"], []).append({"rank": rank, "begin_ns": begin[0]["monotonic_ns"], "end_ns": end[0]["monotonic_ns"]})
        self.result["measurement"] = {"scope": "Per-host monotonic spans; no cross-host subtraction or global weight-ready claim",
            "hosts": {host: {"load_s": (max(r["end_ns"] for r in rows)-min(r["begin_ns"] for r in rows))/1e9, "ranks": rows} for host, rows in by_host.items()}}

    def checkpoint_overlay(self):
        overlay = copy.deepcopy(self.state)
        overlay.update(root_session=str(self.root_session), endpoints=self.endpoints,
                       members=self.members, nodes=NODES, handoff=str(self.handoff), profile=self.profile)
        overlay["models"][self.args.model]["source_ids"] = self.source_ids
        overlay.setdefault("slots", {})[self.slot_name] = self.slot
        if self.args.model == "B" and self.slot_name in ("B", "target_a"):
            overlay["slots"]["B"] = self.slot
            overlay["slots"]["target_a"] = self.slot
        overlay.setdefault("processes", []).extend([(node, str(path)) for node, path in self.processes])
        overlay["new_engine_processes"] = [(node, str(path)) for node, path in self.processes]
        overlay["overlay_parent"] = str(self.input_session)
        overlay["last_bootstrap"] = str(self.out / "summary.json")
        overlay["bootstrap_metrics"] = str(self.metrics)
        write_json(self.out / "session.json", overlay)
        self.result["overlay_session"] = str(self.out / "session.json")

    def cleanup_owned(self):
        for handle in list(reversed(self.processes)):
            try:
                self.stop(handle)
            except Exception as exc:
                self.result["cleanup_errors"].append(repr(exc))

    def run(self):
        try:
            commands = self.commands()
            self.preflight_engine(commands)
            write_json(self.command_path, {"tag": self.phase, "metrics": str(self.metrics), "mode": "restore"})
            started = time.monotonic()
            for node, command, env in commands:
                self.start(node, "fresh_engine_" + node, command, env)
            self.wait_exports()
            self.event("fresh_exports_ready", phase=self.phase)
            targets = [dict(self.replica_target(source, f"{self.phase}/rank{rank}", self.slot["workers"][rank]),
                            mode="live", tag=self.phase, metrics_directory=str(self.metrics))
                       for rank, source in enumerate(self.model["sources"])]
            plan = self.cluster.plan_restore(targets, "resource_lp", self.source_ids)
            self.result["flow"] = self.cluster.transfer(plan, verify_before_ready=True, retain=False)
            expected_bytes = sum(source["n"] for source in self.model["sources"])
            if self.result["flow"]["verification"]["checked_bytes"] != expected_bytes:
                raise AssertionError("SHA verification did not cover every model weight byte")
            self.save()
            # Health endpoints may run a forward pass. Query only non-forward model info,
            # and only after all target bytes have been verified and published.
            self.wait_http(self.slot["endpoint"], "get_model_info", timeout=self.args.timeout)
            repeats = self.args.reference_repeats if self.args.model == "A" else 1
            self.result["reference_diagnostics"] = {
                "scope": "Identical deterministic prompt on the same live engine; no health forward or cache reset between replies",
                "reference": self.model["reference"], "requested_repeats": repeats, "replies": []}
            for repetition in range(repeats):
                reply = self.generate_stream(self.slot)
                first = self.result["reference_diagnostics"]["replies"]
                self.result["reference_diagnostics"]["replies"].append({
                    "repetition": repetition + 1, "reply": reply,
                    "matches_reference_text": reply["text"] == self.model["reference"]["text"],
                    "matches_reference_token_count": reply.get("tokens") == self.model["reference"].get("tokens"),
                    "matches_first_text": not first or reply["text"] == first[0]["reply"]["text"]})
                self.save()
            reply = self.result["reference_diagnostics"]["replies"][0]["reply"]
            self.result["reply"] = reply
            self.result["correct"] = all(row["matches_reference_text"] for row in self.result["reference_diagnostics"]["replies"])
            self.result["bootstrap_to_first_token_s"] = reply["first_token_time"] - started
            self.record_measurement()
            self.save()
            if not self.result["correct"]:
                raise AssertionError("fresh restored engine output differs from saved reference")
            released = time.monotonic()
            request(self.slot["endpoint"], "release_memory_occupation", {"tags": ["weights"]}, timeout=300)
            self.slot["parked"] = True
            self.result["park_s"] = time.monotonic() - released
            self.result["source_hash_mismatches"] = [path for path, sha in self.result["code_sha256"].items()
                if hashlib.sha256((ROOT / path).read_bytes()).hexdigest() != sha]
            if self.result["source_hash_mismatches"]:
                raise RuntimeError("source files changed during bootstrap")
            self.checkpoint_overlay()
            self.result["status"] = "passed"
            self.event("fresh_engine_verified_and_parked", slot=self.slot_name, overlay=self.result["overlay_session"])
        except BaseException as exc:
            self.result.update(status="failed", error=repr(exc))
            self.cleanup_owned()
            self.save()
            raise
        finally:
            self.save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, help="Unique directory below versioned paper_e1_e8 result root")
    parser.add_argument("--session", type=Path, default=ROOT / "codex_coding/results/motivation/fig4_lanes11/session.json")
    parser.add_argument("--resume-session", type=Path, help="Prior overlay; preserve its parked slots")
    parser.add_argument("--model", choices=["A", "B"], default="B")
    parser.add_argument("--node", choices=["node27", "node29"], default="node27")
    parser.add_argument("--slot-name", help="B/target_a for first Qwen; target_b for second Qwen; A for GLM")
    parser.add_argument("--port", type=int, default=32680)
    parser.add_argument("--nccl-port", type=int, default=32780)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--manifest", type=Path, help="Override known verified captured derived.json and adjacent layouts")
    parser.add_argument("--reference-repeats", type=int, choices=[1, 2], default=2,
                        help="GLM only: record this many deterministic replies before checking exact saved-reference text")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535 or not 1024 <= args.nccl_port <= 65535 or args.port == args.nccl_port:
        parser.error("ports must be distinct and in 1024..65535")
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))
    RestoreEngine(args).run()


if __name__ == "__main__":
    main()
