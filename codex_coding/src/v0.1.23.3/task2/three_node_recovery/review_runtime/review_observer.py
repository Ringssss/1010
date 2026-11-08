"""Authenticate the two exact observer snapshots against the preserved GPU child."""
import argparse
import hashlib
import json
from pathlib import Path

RESULTS = Path(__file__).resolve().parents[6] / "codex_coding/results/v0.1.23.3/task2/three_node_recovery"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    observer_dir = RESULTS / "diagnosis30_node28_observer_r01"
    paths = [observer_dir / n for n in ("summary.json", "source.py", "snapshot_2s.json", "snapshot_6s.json")]
    paths += [RESULTS / "probe_node28_observed_r01" / n for n in ("child.process.json", "child.events.jsonl")]
    paths += [RESULTS / "diagnosis30_observed_postcheck_r01" / n for n in ("hylog.txt", "pid.stderr.txt", "command.sh")]
    report = json.loads(paths[0].read_text())
    process = json.loads(paths[4].read_text())
    owner = process["owner"]
    events = [json.loads(line) for line in paths[5].read_text().splitlines()]
    entered = next(e["monotonic_ns"] for e in events if e["stage"] == "set_device_enter")
    checks = {
        "reviewed_observer_source": hashlib.sha256(paths[1].read_bytes()).hexdigest() == "f9a596f07e8d0aba9d42be10e8ae100d2581be02960b9de9999af0334ba70278",
        "same_target_owner_and_event": report["target_owner"] == owner and report["set_device_enter_monotonic_ns"] == entered,
        "two_requested_samples": report["exact_samples"] == 2 and [s["planned_seconds_after_set_device_enter"] for s in report["snapshots"]] == [2, 6],
        "readonly_operations": all(report[k] == 0 for k in ("gpu_operations", "signals", "ptrace_calls", "device_open_calls")),
        "postcheck_absence": all(f"/proc/{pid}/stat: No such file or directory" in paths[7].read_text() for pid in (report["observer"]["pid"], owner["pid"])),
    }
    samples = []
    for i, sample in enumerate(report["snapshots"]):
        checks[f"sample{i}/snapshot_original"] = sample == json.loads(paths[2+i].read_text())
        for label in ("before", "after"):
            actual = sample["owner_" + label]
            checks[f"sample{i}/exact_{label}"] = sample["exact_" + label] and actual["state"] != "Z" and actual["pid_namespace"]["value"] == owner["pid_namespace"] and all(actual[k] == owner[k] for k in ("pid", "start_time", "pgid", "sid", "argv"))
        checks[f"sample{i}/bounded"] = len(sample["threads"]) <= 32 and len(sample["fds"]) <= 16
        threads = []
        for thread in sample["threads"]:
            row = {k: thread[k] for k in ("tid", "wchan", "syscall")}
            if "value" in thread["stat"]:
                fields = thread["stat"]["value"].rsplit(") ", 1)[1].split()
                row.update(state=fields[0], utime_ticks=int(fields[11]), stime_ticks=int(fields[12]), rss_pages=int(fields[21]))
            threads.append(row)
        samples.append({"offset_seconds": sample["actual_seconds_after_set_device_enter"], "threads": threads})
    result = {"evidence_consistent": all(checks.values()), "checks": checks, "samples": samples,
              "hylog": paths[6].read_text(), "runtime_passed": False, "performance_ready": False,
              "scope": "Exact snapshots show CPU and wait states; syscall EPERM remains unknown. Vendor mbind/seccomp message is a causal lead, not measured syscall errno or a unique cause.",
              "input_refs": [{"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in paths]}
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / "source.py").write_bytes(Path(__file__).read_bytes())
    (args.out / "summary.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"evidence_consistent": result["evidence_consistent"], "checks": len(checks)}))
    if not result["evidence_consistent"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
