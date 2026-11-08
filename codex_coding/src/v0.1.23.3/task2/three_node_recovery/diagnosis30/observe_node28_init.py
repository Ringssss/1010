"""Observe one future exact init child without opening devices or sending signals."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import resource
import socket
import time

PROJECT = Path("/home/zhujianian/zhujianian/fluidckpt_zjn")
RESULTS = PROJECT / "codex_coding/results/v0.1.23.3/task2/three_node_recovery"
RUN = RESULTS / "probe_node28_observed_r01"
GATE = PROJECT / "codex_coding/src/v0.1.23.3/task2/three_node_recovery/probe/init_gate.py"
CID = "e693e946828a37dbf4daa3bc25855b1c28f88bd02cdd93ce0d2e95f33e37d3ba"
STARTED = "2026-10-09T06:19:44.292587393Z"
BOOT = "5313e6f8-1712-45dc-9855-4c47358b6c4b"
PIDNS = "pid:[4026541407]"
EXPECTED_ARGV = ["/usr/bin/python3", str(GATE), "--node", "node28", "--container-id", CID,
                 "--container-started-at", STARTED, "--host-boot-id", BOOT,
                 "--expected-container-hostname", "m03r2n08", "--gpu", "2", "--bdf", "0000:55:00.0",
                 "--visibility", "selected", "--stage", "init", "--out", str(RUN), "--worker"]
STATUS_KEYS = {"Name", "State", "Tgid", "Pid", "PPid", "TracerPid", "Threads", "Uid", "Gid",
               "voluntary_ctxt_switches", "nonvoluntary_ctxt_switches", "Cpus_allowed_list", "Mems_allowed_list"}


def read(path, limit=8192):
    try:
        with Path(path).open("rb") as handle:
            raw = handle.read(limit+1)
        return {"value": raw[:limit].decode(errors="replace"), "truncated": len(raw) > limit}
    except OSError as exc:
        return {"error": type(exc).__name__, "errno": exc.errno, "message": str(exc)}


def link(path):
    try:
        return {"value": os.readlink(path)}
    except OSError as exc:
        return {"error": type(exc).__name__, "errno": exc.errno, "message": str(exc)}


def proc(pid):
    row = {"pid": pid}
    stat = read(f"/proc/{pid}/stat")
    row["stat_read"] = stat
    if "error" in stat:
        return row
    try:
        fields = stat["value"].rsplit(") ", 1)[1].split()
        row.update(state=fields[0], pgid=int(fields[2]), sid=int(fields[3]), start_time=fields[19])
    except (IndexError, ValueError) as exc:
        row["parse_error"] = repr(exc)
    cmd = read(f"/proc/{pid}/cmdline", 16384)
    row["cmdline_read"] = cmd
    if "value" in cmd and not cmd["truncated"]:
        row["argv"] = [x for x in cmd["value"].split("\0") if x]
    row["pid_namespace"] = link(f"/proc/{pid}/ns/pid")
    return row


def exact(owner, actual, boot, namespace):
    return (boot == BOOT and namespace == PIDNS and owner.get("boot_id") == BOOT
            and owner.get("pid_namespace") == PIDNS and owner.get("argv") == EXPECTED_ARGV
            and owner.get("pid") == owner.get("pgid") == owner.get("sid")
            and actual.get("state") not in (None, "Z")
            and actual.get("pid_namespace", {}).get("value") == PIDNS
            and all(actual.get(k) == owner.get(k) for k in ("pid", "start_time", "pgid", "sid", "argv")))


def numeric_entries(path, limit):
    try:
        values = sorted((int(p.name) for p in Path(path).iterdir() if p.name.isdigit()))
        return {"total": len(values), "selected": values[:limit], "limit": limit, "omitted": max(0, len(values)-limit)}
    except OSError as exc:
        return {"error": type(exc).__name__, "errno": exc.errno, "message": str(exc), "selected": []}


def snapshot(owner, planned_offset, entered):
    before = proc(owner["pid"])
    row = {"sampled_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
           "monotonic_ns": time.monotonic_ns(), "planned_seconds_after_set_device_enter": planned_offset,
           "actual_seconds_after_set_device_enter": (time.monotonic_ns()-entered)/1e9, "owner_before": before}
    boot = read("/proc/sys/kernel/random/boot_id", 128).get("value", "").strip()
    namespace = link("/proc/self/ns/pid").get("value")
    row["exact_before"] = exact(owner, before, boot, namespace)
    if not row["exact_before"]:
        row["scope_status"] = "exact identity no longer live; no thread/FD reads"
        return row
    pid = owner["pid"]
    threads = numeric_entries(f"/proc/{pid}/task", 32)
    row["thread_selection"] = threads
    row["threads"] = []
    for tid in threads["selected"]:
        prefix = Path(f"/proc/{pid}/task/{tid}")
        status = read(prefix/"status")
        if "value" in status:
            status["value"] = "\n".join(line for line in status["value"].splitlines() if line.partition(":")[0] in STATUS_KEYS)
        row["threads"].append({"tid": tid, "stat": read(prefix/"stat"), "status": status,
                               "wchan": read(prefix/"wchan", 1024), "syscall": read(prefix/"syscall", 1024)})
    fds = numeric_entries(f"/proc/{pid}/fd", 16)
    row["fd_selection"] = fds
    row["fds"] = [{"fd": fd, "symlink": link(f"/proc/{pid}/fd/{fd}")} for fd in fds["selected"]]
    after = proc(pid)
    row["owner_after"] = after
    row["exact_after"] = exact(owner, after, read("/proc/sys/kernel/random/boot_id", 128).get("value", "").strip(), link("/proc/self/ns/pid").get("value"))
    row["scope_status"] = "exact identity preserved" if row["exact_after"] else "identity changed during sample; captured data is not accepted as an exact sample"
    return row


def json_file(path):
    value = read(path, 65536)
    if "error" in value or value.get("truncated"):
        return None
    try:
        return json.loads(value["value"])
    except json.JSONDecodeError:
        return None


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=RESULTS/"diagnosis30_node28_observer_r01")
    args = parser.parse_args()
    args.out = args.out.resolve()
    if args.out.parent != RESULTS or not args.out.name.startswith("diagnosis30_"):
        parser.error("require a new diagnosis30_* result directory")
    assert socket.gethostname() == "m03r2n08" and read("/proc/sys/kernel/random/boot_id", 128).get("value", "").strip() == BOOT
    assert link("/proc/self/ns/pid").get("value") == PIDNS
    args.out.mkdir(exist_ok=False)
    # The observer's own deadline precedes the host wrapper's separate bound.
    started = time.monotonic_ns()
    deadline = started+20_000_000_000
    report = {"target_run": str(RUN), "expected_container_id": CID, "expected_started_at": STARTED,
              "observer": proc(os.getpid()), "started_monotonic_ns": started,
              "hard_deadline_seconds": 20, "planned_offsets_seconds": [2, 6], "snapshots": [],
              "gpu_operations": 0, "signals": 0, "ptrace_calls": 0, "device_open_calls": 0,
              "scope": "one future new own child; PID identity, bounded threads and FD symlinks only"}
    owner = None
    entered = None
    try:
        while time.monotonic_ns() < deadline and entered is None:
            registered = json_file(RUN/"registration.json")
            candidate = json_file(RUN/"child.owner.json")
            event_read = read(RUN/"child.events.jsonl", 65536)
            if registered and candidate and "value" in event_read and not event_read["truncated"]:
                identity = (registered.get("container_id") == CID and registered.get("container_started_at") == STARTED
                            and registered.get("host_boot_id") == BOOT and registered.get("pid_namespace") == PIDNS
                            and registered.get("node") == "node28" and registered.get("gpu") == 2
                            and registered.get("stage") == "init" and registered.get("visibility_policy") == "selected")
                if not identity:
                    report["stop_reason"] = "target registration identity differs"
                    break
                if candidate.get("argv") != EXPECTED_ARGV:
                    report["stop_reason"] = "registered argv differs from the one allowed future run"
                    break
                owner = candidate
                for line in event_read["value"].splitlines():
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if event.get("stage") == "set_device_enter" and event.get("pid") == owner["pid"]:
                        entered = event["monotonic_ns"]
                        break
            time.sleep(0.025)
        report["target_owner"] = owner
        report["set_device_enter_monotonic_ns"] = entered
        if entered is not None:
            for offset in (2, 6):
                due = entered+offset*1_000_000_000
                while time.monotonic_ns() < min(due, deadline):
                    time.sleep(min(0.025, max(0, (min(due, deadline)-time.monotonic_ns())/1e9)))
                if time.monotonic_ns() >= deadline:
                    report["stop_reason"] = "observer total deadline reached"
                    break
                sample = snapshot(owner, offset, entered)
                report["snapshots"].append(sample)
                (args.out/f"snapshot_{offset}s.json").write_text(json.dumps(sample, indent=2)+"\n")
                if not sample["exact_before"] or not sample.get("exact_after"):
                    report["stop_reason"] = "target exact live identity unavailable"
                    break
        else:
            report.setdefault("stop_reason", "set_device event not found before observer deadline")
    except Exception as exc:
        report["error"] = repr(exc)
    finally:
        report["elapsed_seconds"] = (time.monotonic_ns()-started)/1e9
        report["exact_samples"] = sum(s.get("exact_before", False) and s.get("exact_after", False) for s in report["snapshots"])
        report["finished_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        (args.out/"source.py").write_bytes(Path(__file__).read_bytes())
        (args.out/"summary.json").write_text(json.dumps(report, indent=2)+"\n")
        print(json.dumps({"out": str(args.out), "exact_samples": report["exact_samples"], "signals": 0, "gpu_operations": 0}))


if __name__ == "__main__":
    main()
