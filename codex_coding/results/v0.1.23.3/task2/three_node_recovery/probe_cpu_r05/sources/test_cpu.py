"""CPU-only negative contracts and exact-owner deadline validation."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import subprocess
import sys

import init_gate as gate


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out = args.out.resolve()
    if args.out.parent != gate.RESULT_ROOT or not args.out.name.startswith("probe_cpu_"):
        parser.error("require a new probe_cpu_* result directory")
    args.out.mkdir(parents=True, exist_ok=False)
    rows = []
    bdf = "0000:36:00.0"
    gate.admit("00000000:36:00.0", bdf, gate.GUARD+gate.PAYLOAD+1)
    rows.append({"name": "normalized BDF with actual HIP headroom", "passed": True})
    for label, actual, free in (("wrong masked BDF", "0000:09:00.0", gate.GUARD*2),
                                 ("guard equality rejected", bdf, gate.GUARD+gate.PAYLOAD),
                                 ("insufficient free", bdf, gate.GUARD)):
        try:
            gate.admit(actual, bdf, free)
        except ValueError:
            rows.append({"name": label, "passed": True})
        else:
            raise AssertionError(label)
    identity = {"pid": 123, "start_time": "1", "pgid": 123, "sid": 123, "argv": ["fixture"], "live": True}
    for field in gate.IDENTITY:
        bad = dict(identity)
        bad[field] = "mismatch"
        assert not gate.exact(bad, identity), field
        rows.append({"name": "identity rejects changed " + field, "passed": True})
    fixture = str(Path(__file__).with_name("cpu_fixture.py"))
    for label, argv, deadline, signals in (("immediate", [sys.executable, fixture], 1, []),
                                           ("natural", [sys.executable, fixture, "--delay", "0.05"], 1, []),
                                           ("timeout", [sys.executable, fixture, "--delay", "5", "--ignore-term"], 0.2, ["TERM", "KILL"])):
        out = args.out / label
        out.mkdir()
        result = gate.bounded(argv, dict(os.environ), out, deadline)
        assert result["cleanup"]["passed"] and result["cleanup"]["signals"] == signals, result
        assert result["timed_out"] == bool(signals), result
        assert not result["actual_after"]["live"], result
        rows.append({"name": label + " exact bounded process cleanup", "passed": True})
    child = subprocess.Popen([sys.executable, fixture, "--delay", "0.3"], start_new_session=True)
    owner = gate.proc(child.pid)
    refusal = gate.cleanup(child, owner, "different-boot", os.readlink("/proc/self/ns/pid"))
    assert not refusal["passed"] and refusal["signals"] == []
    child.wait(timeout=1)
    rows.append({"name": "cross boot no signal", "passed": True})
    exited = subprocess.Popen([sys.executable, fixture], start_new_session=True)
    exited.wait(timeout=1)
    missing = gate.cleanup(exited, {"pid": exited.pid}, gate.boot_id(), os.readlink("/proc/self/ns/pid"))
    assert missing["passed"] and missing["signals"] == []
    rows.append({"name": "already reaped missing owner fields are safe", "passed": True})
    for label, init_delay, timeout in (("init_before_deadline", 0.03, False), ("init_missing_at_deadline", 0.3, True)):
        out = args.out / label
        out.mkdir()
        argv = [sys.executable, fixture, "--init-event", str(out / "child.events.jsonl"),
                "--init-delay", str(init_delay), "--delay", "0.25"]
        result = gate.bounded(argv, dict(os.environ), out, deadline=1, init_seconds=0.2)
        assert result["timed_out"] == timeout and result["cleanup"]["passed"] and not result["actual_after"]["live"], result
        assert result["init_deadline_passed"] != timeout, result
        rows.append({"name": label, "passed": True})
    out = args.out / "early_exit_no_init"
    out.mkdir()
    result = gate.bounded([sys.executable, fixture], dict(os.environ), out, deadline=1, init_seconds=0.2)
    assert result["returncode"] == 0 and not result["init_deadline_passed"] and result["cleanup"]["passed"], result
    rows.append({"name": "early exit without init event cannot pass deadline", "passed": True})
    prefix = "host:123:456 [1] NCCL INFO "
    loading = prefix + "Loading topology file /owned.xml\n"
    cross = loading + prefix + "rank 1 nRanks 2 nNodes 2 localRanks 1 localRank 0\n" + prefix + "Channel 00 : 0[0] -> 1[0] via NET/IB/0\n"
    assert gate.log_evidence(cross.encode(), 123, 1, "/owned.xml", "cross", 2)["passed"]
    for label, raw in (("unowned log", cross.replace(":123:", ":124:")),
                       ("mapping fallback", cross + prefix + "Loading topology mapping file /default.xml\n"),
                       ("cross requires actual NET", cross.replace("NET/IB/0", "SHM/direct")),
                       ("precise input path", cross.replace("/owned.xml", "/owned.xml.other"))):
        assert not gate.log_evidence(raw.encode(), 123, 1, "/owned.xml", "cross", 2)["passed"], label
        rows.append({"name": label, "passed": True})
    summary = {"passed": all(r["passed"] for r in rows), "checks": rows, "gpu_operations": 0,
               "torch_imported": "torch" in sys.modules, "core_dump_limit": list(resource.getrlimit(resource.RLIMIT_CORE))}
    sources = args.out / "sources"
    sources.mkdir()
    summary["source_refs"] = []
    for name in ("init_gate.py", "test_cpu.py", "cpu_fixture.py"):
        raw = Path(__file__).with_name(name).read_bytes()
        target = sources / name
        target.write_bytes(raw)
        summary["source_refs"].append({"path": str(target), "sha256": hashlib.sha256(raw).hexdigest()})
    gate.write(args.out / "summary.json", summary)
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
