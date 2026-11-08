"""Persist bounded read-only control and exact-owner identity evidence."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import resource
import shlex
import subprocess
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[6]
RESULTS = ROOT / "codex_coding/results/v0.1.23.3/task2"
RUN = RESULTS / "resource_map/xml_startup_owned_r02"
SESSION = RESULTS / "baseline_loading/serverless_target_recovery_r03/session_overlay_owned.json"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + "\n")


def continuity(expected, actual):
    keys = ("boot_id", "container_id", "container_started_at")
    missing = [key for key in keys if not actual.get(key) or not expected.get(key)]
    mismatch = [key for key in keys if expected.get(key) != actual.get(key)]
    return {"passed": not missing and not mismatch, "missing": missing, "mismatch": mismatch,
            "identity_continuity_basis": list(keys), "old_pid_namespace_inode_available": False}


def proc(pid):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1].split()
        return {"pid": pid, "exists": True, "state": fields[0], "live": fields[0] != "Z",
                "start_time": fields[19], "pgid": int(fields[2]),
                "argv": Path(f"/proc/{pid}/cmdline").read_bytes().decode().split("\0")[:-1]}
    except FileNotFoundError:
        return {"pid": pid, "exists": False, "live": False}


def classify(expected, actual, identity_passed):
    if not identity_passed:
        return "identity_continuity_blocked"
    if not actual["exists"]:
        return "exit_evidence_absent"
    if actual.get("start_time") != expected["start_time"]:
        return "pid_reused_blocked"
    if actual.get("state") == "Z":
        return "exit_evidence_zombie"
    if actual.get("pgid") != expected["pid"] or actual.get("argv") != expected["command"]:
        return "owner_mismatch_blocked"
    return "exact_live_cleanup_eligible"


def command(argv, timeout, input_text=None):
    begin = time.monotonic()
    try:
        done = subprocess.run(argv, input=input_text, text=True, capture_output=True, timeout=timeout)
        return dict(argv=argv, returncode=done.returncode, stdout=done.stdout, stderr=done.stderr,
                    elapsed_s=time.monotonic()-begin, timeout_s=timeout)
    except subprocess.TimeoutExpired as exc:
        def decoded(value):
            return value.decode(errors="replace") if isinstance(value, bytes) else (value or "")
        return dict(argv=argv, returncode=None, stdout=decoded(exc.stdout), stderr=decoded(exc.stderr),
                    elapsed_s=time.monotonic()-begin, timeout_s=timeout, timed_out=True)


def container_worker(task, node):
    result = {"node": node, "signals": 0, "gpu_operations": 0,
              "pid_namespace": os.readlink("/proc/1/ns/pid"), "owners": []}
    for row in task["nodes"][node]["owners"]:
        spec_path = Path(row["spec"])
        state_path = spec_path.with_suffix(".pid.json")
        hashes_passed = sha(spec_path) == row["spec_sha256"] and sha(state_path) == row["state_sha256"]
        spec = json.loads(spec_path.read_text())
        recorded = json.loads(state_path.read_text())
        registration_passed = recorded == {"pid": row["pid"], "start_time": row["start_time"], "spec": row["spec"]}
        actual = proc(row["pid"])
        classification = classify(row, actual, hashes_passed and registration_passed)
        result["owners"].append(dict(row, actual=actual, hashes_passed=hashes_passed,
            registration_passed=registration_passed, classification=classification,
            cleanup_eligible=row["kind"] == "old_probe" and classification == "exact_live_cleanup_eligible",
            signal_sent=False))
    for key, path in (("memory_events", "/sys/fs/cgroup/memory.events"), ("host_meminfo", "/proc/meminfo")):
        try:
            result[key] = Path(path).read_text()
        except OSError as exc:
            result[key] = {"error": repr(exc)}
    print(json.dumps(result))


def host_worker(task_path, node):
    task = json.loads(Path(task_path).read_text())
    expected = task["nodes"][node]["identity"]
    result = {"node": node, "signals": 0, "gpu_operations": 0,
              "captured_at": datetime.now(timezone.utc).isoformat()}
    inspected = command(["docker", "inspect", "--format", "{{json .Id}}\n{{json .State}}", expected["container_name"]], 3)
    result["container_inspect"] = inspected
    current = {"boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip()}
    if inspected["returncode"] == 0:
        lines = inspected["stdout"].splitlines()
        container_id, state = json.loads(lines[0]), json.loads(lines[1])
        current.update(container_id=container_id, container_started_at=state["StartedAt"],
                       container_running=state["Running"], container_init_pid=state["Pid"])
    result["identity"] = current
    result["identity_guard"] = continuity(expected, current)
    if result["identity_guard"]["passed"] and current.get("container_running"):
        inside = command(["docker", "exec", expected["container_name"], "python3", str(Path(__file__).resolve()),
                          "--container-worker", "--task", str(task_path), "--node", node], 4)
        result["container_probe"] = inside
        if inside["returncode"] == 0:
            result["container_evidence"] = json.loads(inside["stdout"])
    else:
        result["old_pid_probe_skipped"] = True
    result["control_available"] = bool(result.get("container_evidence"))
    print(json.dumps(result))


def task_spec():
    session = json.loads(SESSION.read_text())
    result = {"session": str(SESSION), "session_sha256": sha(SESSION), "nodes": {}}
    for node in ("node27", "node29"):
        path = RESULTS / "resource_map/provenance_r01" / node / "host.json"
        old = json.loads(path.read_text())
        identity = {"boot_id": old["boot_id"], "container_id": old["container"]["id"],
                    "container_started_at": old["container"]["started"], "container_name": old["container"]["name"]}
        owners = []
        paths = {path for field in ("basic_owned_processes", "recovery_owned_processes", "new_engine_processes")
                 for owner_node, path in session.get(field, []) if owner_node == node}
        paths.update(str(RUN / f"rank_{rank}.process.json") for rank in ((0, 1) if node == "node27" else (2, 3)))
        for name in sorted(paths):
            path = Path(name)
            recorded = json.loads(path.with_suffix(".pid.json").read_text())
            spec = json.loads(path.read_text())
            if recorded["spec"] != str(path):
                raise ValueError("recorded spec path mismatch")
            owners.append(dict(recorded, kind="old_probe" if path.parent == RUN else "protected",
                command=spec["command"], spec_sha256=sha(path), state_sha256=sha(path.with_suffix(".pid.json"))))
        result["nodes"][node] = {"identity": identity, "owners": owners,
            "identity_source": str(RESULTS / "resource_map/provenance_r01" / node / "host.json")}
    return result


def http(endpoint, out):
    begin = time.monotonic()
    result = {"endpoint": endpoint, "timeout_s": 4}
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(endpoint + "/inventory?compact=1", timeout=4) as response:
            raw = response.read()
        target = out / (endpoint.rsplit("/", 1)[-1].replace(":", "_") + ".inventory.json")
        target.write_bytes(raw)
        payload = json.loads(raw)
        result.update(passed=True, raw_path=str(target), response_sha256=hashlib.sha256(raw).hexdigest(),
            worker=payload.get("worker"), session=payload.get("session"), transactions=payload.get("transactions"),
            replica_count=len(payload.get("replicas", [])))
    except Exception as exc:
        result.update(passed=False, error=repr(exc))
    result["elapsed_s"] = time.monotonic()-begin
    return result


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--host-worker", action="store_true")
    parser.add_argument("--container-worker", action="store_true")
    parser.add_argument("--task", type=Path)
    parser.add_argument("--node", choices=("node27", "node29"))
    args = parser.parse_args()
    if args.host_worker or args.container_worker:
        if not args.task or not args.node:
            parser.error("worker requires task/node")
        if args.host_worker:
            host_worker(args.task, args.node)
        else:
            container_worker(json.loads(args.task.read_text()), args.node)
        return
    if not args.out or args.out.resolve().parent != RESULTS / "resource_map" or not args.out.name.startswith("resume_control_"):
        parser.error("require new resource_map/resume_control_* directory")
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    out.chmod(0o777)
    task = task_spec()
    write(out / "task.json", task)
    (out / "source.py").write_bytes(Path(__file__).read_bytes())
    raw_sha = sha(RUN / "summary.json")
    summary = {"status": "running", "signals": 0, "gpu_operations": 0, "ssh": [], "http": [], "icmp": [],
               "captured_at": datetime.now(timezone.utc).isoformat(), "script_sha256": sha(__file__),
               "old_raw_summary_sha256": raw_sha, "exact_cleanup_confirmed": False,
               "protected_postflight_complete": False, "protected_state_corrupted": None}
    write(out / "summary.json", summary)
    jobs = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for node in ("node27", "node29"):
            ips = ("10.211.3.29", "12.11.3.29") if node == "node29" else ("10.211.3.27",)
            for ip in ips:
                remote = shlex.join(["python3", str(Path(__file__).resolve()), "--host-worker", "--task", str(out / "task.json"), "--node", node])
                argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-o", "ConnectionAttempts=1",
                        "-o", "ServerAliveInterval=2", "-o", "ServerAliveCountMax=1", "-o", "Hostname=" + ip, node, remote]
                jobs[pool.submit(command, argv, 12)] = ("ssh", node + "_" + ip.replace(".", "_"))
            for port in (33000, 33001):
                jobs[pool.submit(http, f"http://12.11.3.{node[-2:]}:{port}", out)] = ("http", node + "_" + str(port))
        for ip in ("10.211.3.29", "12.11.3.29"):
            jobs[pool.submit(command, ["ping", "-n", "-c", "1", "-W", "2", ip], 4)] = ("icmp", ip.replace(".", "_"))
        for future in as_completed(jobs):
            kind, name = jobs[future]
            row = future.result()
            if kind == "ssh" and row["returncode"] == 0:
                try:
                    row["evidence"] = json.loads(row["stdout"])
                except ValueError as exc:
                    row["parse_error"] = repr(exc)
            write(out / (kind + "_" + name + ".json"), row)
            summary[kind].append(row)
            write(out / "summary.json", summary)
    summary.update(status="read_only_complete", old_raw_summary_unchanged=sha(RUN / "summary.json") == raw_sha,
        control_available_by_node={node: any(r.get("evidence", {}).get("node") == node and
            r["evidence"].get("control_available") for r in summary["ssh"]) for node in ("node27", "node29")},
        pending_exact_targets=[{"node": "node29", "rank": rank, "pid": pid, "start_time": stamp}
            for rank, pid, stamp in ((2, 21883, "475875814"), (3, 21901, "475875846"))])
    write(out / "summary.json", summary)
    print(json.dumps({"out": str(out), "signals": 0, "gpu_operations": 0,
                      "control_available_by_node": summary["control_available_by_node"]}))


if __name__ == "__main__":
    main()
