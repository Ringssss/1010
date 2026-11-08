"""Prepare or explicitly execute one init/copy launch in a reviewed isolated container."""
import argparse
import base64
import hashlib
import io
import json
from pathlib import Path
import re
import resource
import shlex
import subprocess
import tarfile
import time

ROOT = Path(__file__).resolve().parents[6]
RESULT_ROOT = ROOT / "codex_coding/results/v0.1.23.3/task2/three_node_recovery"
SOURCE_REL = "codex_coding/src/v0.1.23.3/task2/three_node_recovery/probe/init_gate.py"
ALLOW = {"registration.json", "source.py", "child.owner.json", "child.process.json", "child.log",
         "child.events.jsonl", "child.result.json", "summary.json"}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def write(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def reviewed_spec(spec, setup, guard, stage, run_name, source_hash):
    ci = setup["container"]
    gi = guard["container"]
    checks = guard.get("container_identity_checks", {})
    if not checks or not all(checks.values()):
        raise ValueError("preflight guard full container identity not passed")
    if (ci["id"] != gi["id"] or ci["State"]["StartedAt"] != gi["State"]["StartedAt"]
            or gi["State"]["Running"] is not True or gi["image_id"] != spec["image_id"]
            or guard["boot_id"] != spec["boot_id"] or guard["hostname"] != spec["hostname"]):
        raise ValueError("setup/spec/guard identity differs")
    if not re.fullmatch(r"[0-9a-f]{64}", ci["id"]):
        raise ValueError("full container ID required")
    if spec["node"] not in ("node27", "node28", "node30") or not re.fullmatch(r"probe_[a-z0-9_]+", run_name):
        raise ValueError("invalid admitted node or unique result name")
    if spec["ip"] != "12.11.3." + spec["node"][4:]:
        raise ValueError("node/data-IP association differs; node29 excluded")
    host_stage = Path(spec["stage"])
    if host_stage.parent != Path("/tmp/fluidckpt_three_node") or not re.fullmatch(r"[a-z0-9_]+_n(27|28|30)", host_stage.name):
        raise ValueError("unexpected isolated host stage")
    asset = next(a for a in spec["assets"] if a["relative_path"] == SOURCE_REL)
    if sha(base64.b64decode(asset["base64"], validate=True)) != asset["sha256"]:
        raise ValueError("frozen setup source SHA differs")
    observed = guard["source_assets"][SOURCE_REL]
    if observed["sha256"] != asset["sha256"] or observed["expected_sha256"] != asset["sha256"]:
        raise ValueError("preflight staged probe SHA differs from frozen setup")
    return {"node": spec["node"], "ip": spec["ip"], "hostname": spec["hostname"], "boot_id": spec["boot_id"],
            "image_id": spec["image_id"], "container_name": spec["container_name"], "container_id": ci["id"],
            "container_started": ci["State"]["StartedAt"], "host_stage": spec["stage"],
            "gpu": spec["gpu"], "bdf": spec["bdf"], "stage_mode": stage, "run_name": run_name,
            "source_relative_path": SOURCE_REL, "source_sha256": asset["sha256"], "host_source_sha256": source_hash,
            "visibility": "selected", "parent_guard_before_after_required": True}


def extract_archive(bundle, destination):
    raw = base64.b64decode(bundle["base64"], validate=True)
    if len(raw) != bundle["bytes"] or len(raw) > 4 << 20 or sha(raw) != bundle["sha256"]:
        raise ValueError("remote archive SHA/size mismatch")
    refs = {r["name"]: r for r in bundle["artifacts"]}
    if len(refs) != len(bundle["artifacts"]) or not set(refs) <= ALLOW:
        raise ValueError("invalid exact-run artifact manifest")
    destination.mkdir(exist_ok=False)
    seen = set()
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as tar:
        for entry in tar:
            if entry.name not in refs or entry.name in seen or not entry.isfile() or entry.size > 2 << 20:
                raise ValueError("archive includes unowned path or nonregular/oversize entry")
            data = tar.extractfile(entry).read()
            ref = refs[entry.name]
            if len(data) != ref["bytes"] or sha(data) != ref["sha256"]:
                raise ValueError("artifact source SHA mismatch")
            (destination / entry.name).write_bytes(data)
            seen.add(entry.name)
    if seen != set(refs):
        raise ValueError("archive artifact manifest incomplete")
    (destination.parent / "artifacts.tar").write_bytes(raw)
    return [{"name": n, "sha256": refs[n]["sha256"], "bytes": refs[n]["bytes"]} for n in sorted(seen)]


def execute(command, timeout):
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        return {"argv": command, "returncode": process.returncode}, stdout, stderr
    except subprocess.TimeoutExpired as exc:
        process.terminate()
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            pass
        return {"argv": command, "returncode": process.poll(), "timed_out": True, "new_client_pid": process.pid,
                "remote_cleanup": "pending; terminating own SSH/SCP client is not container cleanup"}, exc.stdout or b"", exc.stderr or b""


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--setup", type=Path, required=True)
    parser.add_argument("--guard", type=Path, required=True)
    parser.add_argument("--stage", choices=("init", "copy"), default="init")
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    args.out = args.out.resolve()
    if args.out.parent != RESULT_ROOT or not args.out.name.startswith("probe_launch_"):
        parser.error("require a new versioned probe_launch_* directory")
    host_raw = Path(__file__).with_name("host_gate.py").read_bytes()
    source_inputs = [args.spec, args.setup, args.guard]
    spec, setup, guard = [json.loads(p.read_text()) for p in source_inputs]
    effective = reviewed_spec(spec, setup, guard, args.stage, args.run_name, sha(host_raw))
    args.out.mkdir(parents=True, exist_ok=False)
    effective["input_refs"] = [{"path": str(p.resolve()), "sha256": sha(p.read_bytes())} for p in source_inputs]
    write(args.out / "launch.spec.json", effective)
    (args.out / "host_gate.source.py").write_bytes(host_raw)
    (args.out / "run_gate.source.py").write_bytes(Path(__file__).read_bytes())
    options = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-o", "ConnectionAttempts=1",
               "-o", "StrictHostKeyChecking=yes", "-o", "UpdateHostKeys=no", "-o", "ServerAliveInterval=3",
               "-o", "ServerAliveCountMax=1", "-o", "Hostname="+effective["ip"]]
    record = {"execute": args.execute, "commands": [], "outer_run_deadline_seconds": 35,
              "gpu_operations_started": False, "source_sha256": sha(host_raw), "launch_spec_sha256": sha((args.out / "launch.spec.json").read_bytes())}
    write(args.out / "command.json", record)
    if not args.execute:
        print(json.dumps({"out": str(args.out), "prepared": True, "gpu_operations": 0}))
        return
    start = time.monotonic()
    def invoke(command, cap):
        budget = min(cap, max(0.05, 35-(time.monotonic()-start)))
        row, stdout, stderr = execute(command, budget)
        row["timeout_seconds"] = budget
        record["commands"].append(row)
        write(args.out / "command.json", record)
        if row.get("timed_out") or row["returncode"] != 0:
            (args.out / "failed.stdout.txt").write_bytes(stdout)
            (args.out / "failed.stderr.txt").write_bytes(stderr)
            raise RuntimeError("bounded launch command failed; remote cleanup status pending if launch began")
        return stdout
    remote_temp = invoke(["ssh", "-T", *options, effective["node"],
                          "mktemp -d /tmp/fluidckpt_three_node_launch."+effective["node"]+".XXXXXX"], 6).decode().strip()
    if not re.fullmatch(r"/tmp/fluidckpt_three_node_launch\.node(27|28|30)\.[a-zA-Z0-9]+", remote_temp):
        raise ValueError("unexpected bootstrap directory")
    invoke(["scp", *options, str(args.out / "host_gate.source.py"), str(args.out / "launch.spec.json"),
            effective["node"]+":"+remote_temp+"/"], 6)
    record["gpu_operations_started"] = True
    write(args.out / "command.json", record)
    argv = ["ssh", "-T", *options, effective["node"], shlex.join(["python3", remote_temp+"/host_gate.source.py",
            "--spec", remote_temp+"/launch.spec.json"])]
    raw = invoke(argv, 35)
    (args.out / "stdout.json").write_bytes(raw)
    body = json.loads(raw)
    bundle = body.pop("archive")
    artifacts = extract_archive(bundle, args.out / "artifacts") if bundle else []
    body.update(artifact_refs=artifacts, archive_sha256=bundle["sha256"] if bundle else None,
                source_input_refs=effective["input_refs"], launch_spec_sha256=record["launch_spec_sha256"])
    write(args.out / "result.json", body)
    print(json.dumps({"out": str(args.out), "passed": body["passed"],
                      "child_state": body["child_state"]["status"], "container_cleanup_proven": body["container_cleanup_proven"]}))
    if not body["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
