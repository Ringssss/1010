"""Run one reviewed fresh-container init/copy and archive only its exact artifacts."""
import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import resource
import signal
import subprocess
import sys
import tarfile
import time

PROJECT = Path("/home/zhujianian/zhujianian/fluidckpt_zjn")
RESULT_REL = Path("codex_coding/results/v0.1.23.3/task2/three_node_recovery")
ALLOW = {"registration.json", "source.py", "child.owner.json", "child.process.json", "child.log",
         "child.events.jsonl", "child.result.json", "summary.json"}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def boot():
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def command(argv, timeout):
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    started = time.monotonic_ns()
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        return {"argv": argv, "returncode": process.returncode, "stdout": stdout.decode(errors="replace"),
                "stderr": stderr.decode(errors="replace"), "elapsed_ns": time.monotonic_ns()-started}
    except subprocess.TimeoutExpired as exc:
        # CLI disposal is separate from the container child ownership audit.
        process.terminate()
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            pass
        return {"argv": argv, "returncode": process.poll(), "timed_out": True,
                "stdout": (exc.stdout or b"").decode(errors="replace"),
                "stderr": (exc.stderr or b"").decode(errors="replace"),
                "new_cli_pid": process.pid, "cli_disposal": "TERM own CLI only; not container cleanup",
                "container_cleanup_proven": False, "elapsed_ns": time.monotonic_ns()-started}


def inspect(name):
    fmt = '{"id":{{json .Id}},"started":{{json .State.StartedAt}},"running":{{json .State.Running}},"image":{{json .Image}},"task_label":{{json (index .Config.Labels "fluidckpt.task")}}}'
    row = command(["docker", "inspect", "--format", fmt, name], 3)
    if row["returncode"] != 0:
        raise RuntimeError("exact container inspect failed: " + row["stderr"])
    return json.loads(row["stdout"])


def identity(row, spec):
    return (boot() == spec["boot_id"] and os.uname().nodename == spec["hostname"]
            and row["id"] == spec["container_id"] and row["started"] == spec["container_started"]
            and row["running"] is True and row["image"] == spec["image_id"]
            and row["task_label"] == "v0.1.23.3/task2/three_node_recovery")


def process_state(pid):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
        argv = [v.decode() for v in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0") if v]
        return {"pid": pid, "start_time": fields[19], "state": fields[0], "live": fields[0] != "Z",
                "pgid": int(fields[2]), "sid": int(fields[3]), "argv": argv}
    except FileNotFoundError:
        return {"pid": pid, "state": "absent", "live": False}


def owner_matches(actual, owner):
    if actual.get("state") == "absent":
        return True
    if actual.get("state") == "Z":
        return actual.get("start_time") == owner.get("start_time")
    return actual.get("live") and all(actual.get(k) == owner.get(k) for k in ("pid", "start_time", "pgid", "sid", "argv"))


def child_state(directory):
    path = Path(directory)
    owner_path = path / "child.owner.json"
    if not owner_path.exists():
        return {"status": "unknown", "reason": "no registered child owner", "signals": 0}
    owner = json.loads(owner_path.read_text())
    if boot() != owner["boot_id"] or os.readlink("/proc/self/ns/pid") != owner["pid_namespace"]:
        return {"status": "unknown", "reason": "boot/PID namespace mismatch", "signals": 0}
    actual = process_state(owner["pid"])
    matches = owner_matches(actual, owner)
    return {"owner": owner, "actual": actual, "owner_matches": matches,
            "status": "exited" if matches and not actual["live"] else "live_exact" if matches else "mismatch",
            "signals": 0}


def archive(directory):
    buffer = io.BytesIO()
    refs = []
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for path in sorted(directory.iterdir()):
            if path.name not in ALLOW or not path.is_file() or path.is_symlink():
                raise ValueError("unexpected file in exact run directory")
            raw = path.read_bytes()
            if len(raw) > 2 << 20:
                raise ValueError("run artifact exceeds 2MiB bound")
            refs.append({"name": path.name, "bytes": len(raw), "sha256": sha(raw)})
            info = tarfile.TarInfo(path.name)
            info.size = len(raw)
            tar.addfile(info, io.BytesIO(raw))
    raw = buffer.getvalue()
    if len(raw) > 4 << 20:
        raise ValueError("exact run archive exceeds 4MiB bound")
    return {"sha256": sha(raw), "bytes": len(raw), "artifacts": refs, "base64": base64.b64encode(raw).decode()}


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path)
    parser.add_argument("--child-state", type=Path)
    args = parser.parse_args()
    if args.child_state:
        print(json.dumps(child_state(args.child_state)))
        return
    spec = json.loads(args.spec.read_text())
    if sha(Path(__file__).read_bytes()) != spec["host_source_sha256"]:
        raise ValueError("host launcher source SHA mismatch")
    if spec["node"] not in ("node27", "node28", "node30") or spec["stage_mode"] not in ("init", "copy"):
        raise ValueError("invalid admitted node/stage")
    before = inspect(spec["container_name"])
    if not identity(before, spec):
        raise ValueError("fresh container identity changed before launch")
    host_project = Path(spec["host_stage"]) / "project"
    source = host_project / spec["source_relative_path"]
    if sha(source.read_bytes()) != spec["source_sha256"]:
        raise ValueError("staged probe source SHA mismatch")
    host_out = host_project / RESULT_REL / spec["run_name"]
    if host_out.exists():
        raise ValueError("remote result already exists; no overwrite")
    inside_out = PROJECT / RESULT_REL / spec["run_name"]
    host_helper = host_project / "codex_coding/src/v0.1.23.3/task2/three_node_recovery/probe" / ("host_gate_" + spec["run_name"] + ".py")
    if host_helper.exists():
        raise ValueError("run-specific state reader already exists")
    host_helper.write_bytes(Path(__file__).read_bytes())
    inside_helper = PROJECT / host_helper.relative_to(host_project)
    argv = ["docker", "exec", "-e", "PYTHONDONTWRITEBYTECODE=1", spec["container_id"], "python3",
            str(PROJECT / spec["source_relative_path"]), "--node", spec["node"],
            "--container-id", spec["container_id"], "--container-started-at", spec["container_started"],
            "--host-boot-id", spec["boot_id"], "--expected-container-hostname", spec["hostname"],
            "--gpu", str(spec["gpu"]), "--bdf", spec["bdf"], "--visibility", "selected",
            "--stage", spec["stage_mode"], "--out", str(inside_out)]
    run = command(argv, 20)
    after = inspect(spec["container_name"])
    continuous = identity(after, spec)
    state = {"status": "unknown", "reason": "container identity changed after launch", "signals": 0}
    if continuous:
        query = command(["docker", "exec", spec["container_id"], "python3", str(inside_helper),
                         "--child-state", str(inside_out)], 2)
        state = json.loads(query["stdout"]) if query["returncode"] == 0 else {"status": "unknown", "command": query, "signals": 0}
    bundle = archive(host_out) if host_out.is_dir() else None
    summary = json.loads((host_out / "summary.json").read_text()) if (host_out / "summary.json").exists() else None
    frozen = (host_out / "source.py")
    source_preserved = sha(source.read_bytes()) == spec["source_sha256"] and frozen.exists() and sha(frozen.read_bytes()) == spec["source_sha256"]
    passed = (run["returncode"] == 0 and not run.get("timed_out") and continuous and source_preserved
              and summary is not None and summary["passed"] and summary["process"]["cleanup"]["passed"]
              and state["status"] == "exited")
    result = {"passed": passed, "run": run, "container_before": before, "container_after": after,
              "identity_continuous": continuous, "source_preserved": source_preserved,
              "child_state": state, "probe_summary": summary, "archive": bundle,
              "remote_result": str(host_out), "container_cleanup_proven": bool(summary and summary["process"]["cleanup"]["passed"] and state["status"] == "exited"),
              "protected_state_scope": "parent guard before/after required; this launcher only proves own process/identity",
              "gpu_signals_by_host_wrapper": 0}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
