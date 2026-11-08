"""CPU-only launch identity and exact artifact collection negatives."""
import argparse
import copy
import hashlib
import json
from pathlib import Path

import host_gate
import run_gate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--setup", type=Path, required=True)
    parser.add_argument("--guard", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out = args.out.resolve()
    if args.out.parent != run_gate.RESULT_ROOT or not args.out.name.startswith("probe_launch_cpu_"):
        parser.error("require new probe_launch_cpu_* result directory")
    args.out.mkdir(parents=True, exist_ok=False)
    spec, setup, guard = [json.loads(p.read_text()) for p in (args.spec, args.setup, args.guard)]
    host_hash = run_gate.sha(Path(host_gate.__file__).read_bytes())
    run_gate.reviewed_spec(spec, setup, guard, "copy", "probe_cpu_review", host_hash)
    rows = [{"name": "actual reviewed setup/guard admits CPU plan", "passed": True}]
    owner = {"pid": 10, "start_time": "1", "pgid": 10, "sid": 10, "argv": ["fixture"]}
    assert host_gate.owner_matches({"pid": 10, "state": "absent", "live": False}, owner)
    assert host_gate.owner_matches({"pid": 10, "state": "Z", "live": False, "start_time": "1"}, owner)
    assert not host_gate.owner_matches({"pid": 10, "state": "Z", "live": False, "start_time": "2"}, owner)
    rows.append({"name": "reused Z PID cannot be counted as own exit", "passed": True})
    for label, target, field, bad in (("boot change", "guard", "boot_id", "changed"),
                                      ("wrong data node IP", "spec", "ip", "12.11.3.29")):
        inputs = {"spec": copy.deepcopy(spec), "setup": copy.deepcopy(setup), "guard": copy.deepcopy(guard)}
        inputs[target][field] = bad
        try:
            run_gate.reviewed_spec(inputs["spec"], inputs["setup"], inputs["guard"], "copy", "probe_cpu_review", host_hash)
        except ValueError:
            rows.append({"name": label + " rejected before SSH", "passed": True})
        else:
            raise AssertionError(label)
    changed = copy.deepcopy(guard)
    changed["container"]["State"]["StartedAt"] = "changed"
    try:
        run_gate.reviewed_spec(spec, setup, changed, "copy", "probe_cpu_review", host_hash)
    except ValueError:
        rows.append({"name": "container restart rejected", "passed": True})
    else:
        raise AssertionError("restart")
    changed = copy.deepcopy(guard)
    changed["source_assets"][run_gate.SOURCE_REL]["sha256"] = "changed"
    try:
        run_gate.reviewed_spec(spec, setup, changed, "copy", "probe_cpu_review", host_hash)
    except ValueError:
        rows.append({"name": "staged source change rejected", "passed": True})
    else:
        raise AssertionError("source change")
    fixture = args.out / "fixture"
    fixture.mkdir()
    (fixture / "summary.json").write_text('{"passed": false}\n')
    bundle = host_gate.archive(fixture)
    run_gate.extract_archive(bundle, args.out / "extracted")
    assert (args.out / "extracted/summary.json").read_bytes() == (fixture / "summary.json").read_bytes()
    rows.append({"name": "only-run archive SHA roundtrip", "passed": True})
    broken = copy.deepcopy(bundle)
    broken["artifacts"][0]["sha256"] = "changed"
    try:
        run_gate.extract_archive(broken, args.out / "broken_extract")
    except ValueError:
        rows.append({"name": "artifact SHA mismatch rejected", "passed": True})
    else:
        raise AssertionError("artifact SHA")
    (fixture / "other-user-file").write_text("fixture")
    try:
        host_gate.archive(fixture)
    except ValueError:
        rows.append({"name": "unexpected file cannot enter archive", "passed": True})
    else:
        raise AssertionError("unowned file")
    refs = []
    for name in ("run_gate.py", "host_gate.py", "test_launch_cpu.py"):
        raw = Path(__file__).with_name(name).read_bytes()
        target = args.out / name
        target.write_bytes(raw)
        refs.append({"path": str(target), "sha256": hashlib.sha256(raw).hexdigest()})
    summary = {"passed": all(r["passed"] for r in rows), "checks": rows, "source_refs": refs,
               "gpu_operations": 0, "ssh_commands": 0, "docker_commands": 0}
    run_gate.write(args.out / "summary.json", summary)
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
