"""Join saved SMI PID allocations to host process/cgroup identities."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import re

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    snapshot_path, identities_path = args.run / "snapshot.json", args.run / "identities.json"
    snapshot, identities = json.loads(snapshot_path.read_text()), json.loads(identities_path.read_text())
    output = {"source_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (snapshot_path, identities_path)}, "nodes": {}}
    for node in ("node27", "node29"):
        data, identity = snapshot["nodes"][node]["data"], identities["nodes"][node]["data"]
        names = dict(line.split(maxsplit=1) for line in identity["container_names"].splitlines())
        process_map = {p["host_pid"]: p for p in identity["processes"]}
        cpu = {p["pid"]: p["cpu_percent_one_core"] for p in data["cpu_sample"]["top_processes"]}
        selected = []
        for block in re.split(r"\nPID:\s*", data["smi"]["pids"]["stdout"])[1:]:
            pid = int(block.splitlines()[0])
            process = process_map.get(pid)
            if not process or process["comm"] != "VLLM::Worker_TP":
                continue
            gpu = re.search(r"HCU Index:\s*(\[[^\n]*\])", block)
            bus = re.search(r"PCI BUS:\s*(\[[^\n]*\])", block)
            vram = re.search(r"VRAM USED\(MiB\):\s*(\d+)", block)
            docker = re.search(r"docker-([a-f0-9]{64})", process["cgroup"])
            selected.append({"host_pid": pid, "gpu_indices": ast.literal_eval(gpu[1]) if gpu else [], "bdf": ast.literal_eval(bus[1]) if bus else [], "vram_mib": int(vram[1]), "comm": process["comm"], "start_utc": process["start_utc"], "start_time_ticks": process["start_time_ticks"], "age_seconds_at_identity_sample": process["age_seconds"], "container": names.get(docker[1][:12]) if docker else None, "cgroup": process["cgroup"], "cpu_percent_one_core_at_load_sample": cpu.get(pid), "cgroup_cpu": process.get("cgroup_files", {})})
        output["nodes"][node] = {"load_sample_utc": data["utc_begin"], "identity_sample_utc": identity["utc"], "loadavg": data["loadavg"], "vllm_processes": sorted(selected, key=lambda x: x["gpu_indices"]), "hcu_usage_memory": json.loads(data["smi"]["memory_use"]["stdout"]), "owned_container_cgroup": data["owned_container"]["cgroup"]["files"]}
    after_path = args.run.parent / "provenance_r01/runtime_after.json"
    after = json.loads(after_path.read_text())
    comparisons = []
    for node, record in after["nodes"].items():
        before = json.loads((args.run.parent / "provenance_r01" / node / "container.json").read_text())
        expected = {f["path"]: f["sha256"] for f in before["files"] if f.get("sha256")}
        for line in record["data"].splitlines():
            actual, path = line.split(maxsplit=1)
            comparisons.append({"node": node, "path": path, "before_sha": expected[path], "after_sha": actual, "passed": expected[path] == actual})
    output["after_file_stability"] = {"source": str(after_path), "checks": comparisons, "passed": all(c["passed"] for c in comparisons)}
    destination = args.run / "summary.json"
    if destination.exists():
        raise FileExistsError(destination)
    destination.write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps({"out": str(destination), "vllm_per_node": {n: len(v["vllm_processes"]) for n, v in output["nodes"].items()}, "after_file_stability": output["after_file_stability"]["passed"]}))

if __name__ == "__main__":
    main()
