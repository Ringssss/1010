"""Deadline-bound local protection snapshot and exact owned-process evidence."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import resource
import signal
import time

from common import CONTAINER, NODE, ORIGINAL_FILES, ROOT, digest, inventory, local_state, owned, write


def event(out, phase, **values):
    with (out / "snapshot.events.jsonl").open("a") as handle:
        handle.write(json.dumps({"phase": phase, "pid": os.getpid(), "monotonic_ns": time.monotonic_ns(), **values}) + "\n")
        handle.flush()


def identity(spec):
    spec = Path(spec)
    registration = json.loads(spec.with_suffix(".pid.json").read_text())
    pid = registration["pid"]
    state = inventory.proc_state(pid)
    return {"spec": str(spec), "spec_sha256": digest(spec), "registered": registration, "actual": state,
            "live_command_sha256": hashlib.sha256(Path(f"/proc/{pid}/cmdline").read_bytes()).hexdigest()
                if state["live"] else None,
            "pgid": os.getpgid(pid) if state["live"] else None,
            "passed": state["live"] and state.get("start_time") == registration["start_time"]}


def snapshot(args):
    state = local_state(args.session)
    event(args.out, "node_snapshot_enter", operation="inventory.node_snapshot/discover", deadline_seconds=args.deadline)
    value = inventory.node_snapshot(args.session, NODE, "shca_0", "ib0")
    event(args.out, "node_snapshot_exit")
    paths = [p["spec"] for p in value["protected_processes"]]
    if len(paths) != 3:
        raise ValueError("expected exactly two local keepers and one parked source engine")
    value["protected_processes"] = [identity(path) for path in paths]
    if any(not p["passed"] for p in value["protected_processes"]):
        raise ValueError("local protected PID/starttime failed")
    value["original_xml"] = [{"path": p, "sha256": digest(p), "bytes": Path(p).stat().st_size} for p in ORIGINAL_FILES]
    value["core_dump_limit"] = list(resource.getrlimit(resource.RLIMIT_CORE))
    value["effective_visibility"] = {k: os.environ.get(k) for k in ("HIP_VISIBLE_DEVICES", "CUDA_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES")}
    value["scope"] = "node27 only; no engine health, node29 RPC or signal"
    return value


def evidence(args):
    spec = args.spec.resolve()
    if spec.parent != args.out or spec.name not in ("rank_0.process.json", "rank_1.process.json"):
        raise ValueError("only the two isolated owned rank specs are allowed")
    expected = json.loads(spec.with_suffix(".pid.json").read_text())
    actual = inventory.proc_state(expected["pid"])
    value = {"registered": expected, "actual": actual, "spec_sha256": digest(spec)}
    if args.check_stop:
        if actual.get("start_time") not in (None, expected["start_time"]):
            raise ValueError("PID reused: exact cleanup rejected")
        if actual["live"]:
            argv = Path(f"/proc/{expected['pid']}/cmdline").read_bytes().split(b"\0")
            command = [str(x).encode() for x in json.loads(spec.read_text())["command"]]
            if argv[-1:] == [b""]:
                argv.pop()
            if argv != command or os.getpgid(expected["pid"]) != expected["pid"]:
                raise ValueError("exact owned argv/PGID mismatch: cleanup rejected")
        value["stop_admitted"] = True
    if args.artifact:
        path = args.artifact.resolve()
        rank = int(spec.name.split("_")[1].split(".")[0])
        allowed = {f"rank_{rank}.{suffix}" for suffix in ("environment.json", "libraries.json", "topology.xml", "json", "events.jsonl", "log")}
        allowed.add(f"rank_{rank}.nccl.{expected['pid']}.log")
        if path.parent != args.out or path.name not in allowed:
            raise ValueError("artifact outside exact local rank evidence allowlist")
        value.update(path=str(path), exists=path.exists())
        if path.exists():
            raw = path.read_bytes()
            if len(raw) > 16 << 20:
                raise ValueError("artifact exceeds the 16MiB evidence bound")
            value.update(sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw), content_base64=base64.b64encode(raw).decode())
    return value


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--deadline", type=int, default=30)
    parser.add_argument("--snapshot", action="store_true")
    parser.add_argument("--spec", type=Path)
    parser.add_argument("--check-stop", action="store_true")
    parser.add_argument("--artifact", type=Path)
    parser.add_argument("--bundle", action="store_true")
    args = parser.parse_args()
    args.out = owned(args.out)
    if not 1 <= args.deadline <= 30:
        parser.error("read deadline must be 1..30s")
    def expired(signum, frame):
        event(args.out, "alarm_expired")
        raise TimeoutError("local read deadline expired")
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(args.deadline)
    event(args.out, "helper_enter", pre_torch_import=True)
    value = snapshot(args) if args.snapshot else evidence(args)
    if args.bundle:
        rank = int(args.spec.name.split("_")[1].split(".")[0])
        names = [f"rank_{rank}.{s}" for s in ("environment.json", "libraries.json", "topology.xml", "json", "events.jsonl", "log")]
        names.append(f"rank_{rank}.nccl.{value['registered']['pid']}.log")
        value["artifacts"] = []
        for name in names:
            args.artifact = args.out / name
            value["artifacts"].append(evidence(args))
    print(json.dumps(value))


if __name__ == "__main__":
    main()
