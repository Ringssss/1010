"""Collect an already stopped layout session without Python-version-specific tar extraction."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tarfile

import control_layout as control


def extract_regular(archive, destination, prefixes):
    with tarfile.open(archive) as handle:
        members = handle.getmembers()
        seen = set()
        if sum(member.size for member in members) > 64 << 20:
            raise ValueError("owned artifact archive exceeds 64MiB bound")
        for member in members:
            relative = Path(member.name)
            if (relative.is_absolute() or ".." in relative.parts or not relative.parts
                    or relative.parts[0] not in prefixes or member.name in seen
                    or not (member.isdir() or member.isfile())):
                raise ValueError("unsafe owned archive entry: " + member.name)
            seen.add(member.name)
        destination.mkdir(parents=True, exist_ok=False)
        for member in members:
            target = destination / member.name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with handle.extractfile(member) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.plan, args.out = args.plan.resolve(), args.out.resolve()
    if args.out.parent != control.RESULTS or args.out.name != "operations_collect_r02":
        raise ValueError("require the fresh collect-only r02 directory")
    original_source = control.HERE / "control_layout.py"
    if hashlib.sha256(original_source.read_bytes()).hexdigest() != "f3154366e462c480aa41d513663a8302b18fa83ef13d25e7c871478dcd848ab6":
        raise ValueError("original executed controller source changed")
    args.out.mkdir(parents=True, exist_ok=False)
    plan = json.loads(args.plan.read_text())
    summary = {"status": "collecting", "source_ref": control.old.ref(Path(__file__)),
               "original_controller_ref": control.old.ref(original_source),
               "actual_launch_plan_ref": control.old.ref(args.plan), "GPU_launches": 0, "signals": 0,
               "original_outer_summary_ref": control.old.ref(control.RESULTS / "operations_run_r01/summary.json"),
               "collection_failure_preserved": "operations_collect_r01 tar.extractall(filter='data') TypeError; parent outer rc1 retained",
               "original_outer_status_interpretation": "prelaunch label is stale after successful app/controller/two supervisors; original remains untouched",
               "nodes": {}}
    control.old.write(args.out / "collection_result.json", summary)
    control.fresh_guards(args.out, "after_stop")
    for worker in plan["workers"]:
        node = worker["node"]
        control.metadata(worker, args.plan, args.out, "metadata_" + node, final=True)
        remote = ["docker", "exec", "-i", worker["container_id"], "python3", "-", "--owner",
                  str(control.MIN / control.WORKER_NAME.format(node=node) / "worker.owner.json")]
        process, stdout = control.old.record(control.old.ssh(node, remote), args.out / ("exact_owner_" + node), 15,
                                             (control.HERE / "exact_owned_readonly.py").read_bytes())
        if stdout:
            control.old.write(args.out / ("exact_owner_" + node) / "stdout.json", json.loads(stdout))
        if process["returncode"] != 0:
            raise RuntimeError("fresh exact owned process absence/group-empty failed")
        stage = "/tmp/fluidckpt_three_node/minimal_measure_r01_n" + node[-2:] + "/project/"
        remote_root = stage + str(control.MIN.relative_to(control.ROOT))
        worker_name = control.WORKER_NAME.format(node=node)
        argv = control.old.ssh(node, ["tar", "-C", remote_root, "-cf", "-", worker_name, control.DUMP_NAME])
        process, stdout = control.old.record(argv, args.out / ("collect_" + node), 20)
        if process["returncode"] != 0:
            raise RuntimeError("owned artifact collection failed")
        archive = args.out / ("collect_" + node) / "worker.tar"
        archive.write_bytes(stdout)
        extract_regular(archive, archive.parent / "raw", {worker_name, control.DUMP_NAME})
        supervisor_path = archive.parent / "raw" / worker_name / "supervisor.summary.json"
        supervisor = json.loads(supervisor_path.read_text())
        baseline_path = control.RESULTS / "operations_baseline_r01" / ("baseline_" + node) / "stdout.json"
        baseline = json.loads(baseline_path.read_text())
        baseline_bytes = int(baseline["container_cgroup"]["files"]["memory.current"])
        samples = supervisor["cgroup_samples"]
        maximum = max(int(row["memory.current"]) for row in samples if row.get("memory.current") is not None)
        delta = maximum - baseline_bytes
        after = json.loads((args.out / ("after_stop_" + node) / "stdout.json").read_text())
        event_lines = set(after["container_cgroup"]["files"]["memory.events"].splitlines())
        summary["nodes"][node] = {"supervisor_ref": control.old.ref(supervisor_path),
              "baseline_ref": control.old.ref(baseline_path), "global_preworker_baseline_bytes": baseline_bytes,
              "lifetime_sample_count": len(samples), "lifetime_observed_max_bytes": maximum,
              "lifetime_observed_increment_bytes": delta, "original_DRAM_budget_bytes": 2147483648,
              "observed_budget_margin_bytes": 2147483648-delta, "lifetime_observed_budget_passed": delta <= 2147483648,
              "kernel_peak_missing": all(row.get("memory.peak") is None for row in samples),
              "physical_peak_verified": False, "sampling_period_s": 0.25,
              "supervisor_cleanup_passed": supervisor.get("cleanup", {}).get("passed") is True,
              "supervisor_stop_reason": supervisor.get("stop_reason"),
              "fresh_exact_absent_groupempty": json.loads((args.out / ("exact_owner_" + node) / "stdout.json").read_text())["passed"],
              "after_container_pids": after["container_cgroup"]["files"]["pids.current"],
              "after_no_OOM": {"oom 0", "oom_kill 0"}.issubset(event_lines), "tar_ref": control.old.ref(archive)}
        control.old.write(args.out / "collection_result.json", summary)
    summary["status"] = "collection_completed_pending_independent_review"
    control.old.write(args.out / "collection_result.json", summary)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
