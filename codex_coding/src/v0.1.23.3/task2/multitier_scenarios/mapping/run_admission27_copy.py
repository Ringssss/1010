"""Parent-controlled node27 copy entry using the unchanged 60s supervisor."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import resource
import subprocess

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")

def inspect(name):
    p = subprocess.run(["docker", "inspect", name], capture_output=True, timeout=5)
    if p.returncode:
        raise RuntimeError("docker inspect failed: " + p.stderr.decode(errors="replace"))
    c = json.loads(p.stdout)[0]
    return {"id": c["Id"], "name": c["Name"], "image_id": c["Image"], "State": c["State"],
            "memory_cap_bytes": c["HostConfig"]["Memory"]}

def cgroup_snapshot(pid):
    raw = Path(f"/proc/{pid}/cgroup").read_text()
    group = next(s.split(":", 2)[2] for s in raw.splitlines() if s.startswith("0::"))
    root = Path("/sys/fs/cgroup") / group.lstrip("/")
    return {"path": str(root), **{key: (root / key).read_text().strip() for key in
                                 ("memory.current", "memory.max", "memory.events", "pids.current")}}


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--setup", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.out.parent.name != "multitier_scenarios" or not args.out.name.startswith("mapping_"):
        parser.error("fresh mapping_* local output required")
    spec = json.loads((args.plan / "node27.setup.spec.json").read_text())
    planned = json.loads((args.plan / "plan.json").read_text())
    setup = json.loads(args.setup.read_text())
    c = setup["container"]
    if os.uname().nodename != spec["hostname"] or Path("/proc/sys/kernel/random/boot_id").read_text().strip() != spec["boot_id"]:
        raise ValueError("node27 current host/boot differs")
    if not setup["run"]["returncode"] == 0 or not c["State"]["Running"] or c["image_id"] != spec["image_id"]:
        raise ValueError("setup not passed")
    relative = planned["copy_runtime_source"]
    stage = Path(spec["stage"]) / "project"
    if sha(stage / relative) != planned["copy_runtime_source_sha256"]:
        raise ValueError("staged runtime source differs")
    project = "/home/zhujianian/zhujianian/fluidckpt_zjn"
    runtime_out = project + "/codex_coding/results/v0.1.23.3/task2/three_node_recovery/probe_multitier_node27_copy60_r01"
    argv = ["docker", "exec", "-e", "OMP_NUM_THREADS=1", "-e", "MKL_NUM_THREADS=1", c["id"], "python3",
            project + "/" + relative, "--node", "node27", "--container-id", c["id"],
            "--container-started-at", c["State"]["StartedAt"], "--host-boot-id", spec["boot_id"],
            "--expected-container-hostname", spec["hostname"], "--gpu", "0", "--bdf", spec["bdf"],
            "--visibility", "selected", "--stage", "copy", "--out", runtime_out]
    args.out.mkdir(exist_ok=False)
    before = inspect(c["id"])
    if (before["id"] != c["id"] or before["State"]["StartedAt"] != c["State"]["StartedAt"]
            or before["State"]["Running"] is not True or before["memory_cap_bytes"] != 6 << 30):
        raise ValueError("fresh exact container identity/cap differs")
    protected = [inspect(name) for name in spec["protected_containers"]]
    cg_before = cgroup_snapshot(before["State"]["Pid"])
    if cg_before["pids.current"] != "1":
        raise ValueError("fresh container is not quiet before launch")
    save(args.out / "prepared_launch.json", {"argv": argv, "execute": args.execute, "timeout_seconds": 75,
                                              "before": before, "protected_before": protected, "cgroup_before": cg_before,
                                              "runtime_source_sha256": planned["copy_runtime_source_sha256"],
                                              "setup_sha256": sha(args.setup), "plan_sha256": sha(args.plan / "plan.json")})
    if not args.execute:
        print(json.dumps({"prepared": True, "out": str(args.out), "gpu_calls": 0}))
        return
    record = {"started_utc": datetime.now(timezone.utc).isoformat(), "argv": argv}
    child = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    record["own_docker_cli_pid"] = child.pid
    try:
        stdout, stderr = child.communicate(timeout=75)
        record["returncode"] = child.returncode
    except subprocess.TimeoutExpired as exc:
        stdout, stderr = exc.stdout or b"", exc.stderr or b""
        record.update(returncode=None, timed_out=True, pending_own_cli_pid=child.pid,
                      remote_cleanup="pending: inspect unchanged 60s supervisor evidence; no broad signal")
    (args.out / "stdout.txt").write_bytes(stdout)
    (args.out / "stderr.txt").write_bytes(stderr)
    runtime_host = stage / runtime_out.removeprefix(project + "/")
    artifacts = args.out / "artifacts"
    artifacts.mkdir()
    allow = ("registration.json", "source.py", "child.owner.json", "child.process.json", "child.log",
             "child.events.jsonl", "child.result.json", "summary.json")
    for name in allow:
        path = runtime_host / name
        if path.is_file() and path.stat().st_size <= 2 << 20:
            (artifacts / name).write_bytes(path.read_bytes())
    record.update(ended_utc=datetime.now(timezone.utc).isoformat(), after=inspect(c["id"]),
                  protected_after=[inspect(name) for name in spec["protected_containers"]])
    record["cgroup_after"] = cgroup_snapshot(record["after"]["State"]["Pid"])
    record["protected_exact_unchanged"] = record["protected_after"] == protected
    record["fresh_container_identity_preserved"] = (record["after"]["id"] == before["id"]
        and record["after"]["State"]["StartedAt"] == before["State"]["StartedAt"] and record["after"]["State"]["Running"] is True)
    def events(raw):
        return dict(line.split() for line in raw.splitlines())
    a, b = events(cg_before["memory.events"]), events(record["cgroup_after"]["memory.events"])
    record["no_new_oom"] = all(a.get(k) == b.get(k) for k in ("oom", "oom_kill"))
    record["fresh_container_quiet_after"] = record["cgroup_after"]["pids.current"] == "1"
    summary = artifacts / "summary.json"
    record["runtime_passed"] = json.loads(summary.read_text()).get("passed", False) if summary.exists() else False
    record["passed"] = (record.get("returncode") == 0 and record["runtime_passed"] and record["protected_exact_unchanged"]
                        and record["fresh_container_identity_preserved"] and record["no_new_oom"] and record["fresh_container_quiet_after"])
    record["qualification_scope"] = "node27 GPU0 fresh 1MiB copy only; aggregate HBM cap/network/SSD/six-worker unknown"
    save(args.out / "result.json", record)
    print(json.dumps({"passed": record["passed"], "out": str(args.out)}))
    if not record["passed"]:
        raise SystemExit(1)

if __name__ == "__main__":
    main()
