"""Verify frozen evidence identities and cross-representation alignment on CPU."""
import argparse
import hashlib
import json
from pathlib import Path

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    checks = []
    def check(name, result):
        checks.append({"name": name, "passed": bool(result)})
    manifest = json.loads((args.run / "manifest.json").read_text())
    for item in manifest["files"]:
        check(f"raw_sha:{item['path']}", sha(item["path"]) == item["sha256"])
        check(f"raw_bytes:{item['path']}", Path(item["path"]).stat().st_size == item["bytes"])
    for command in manifest["commands"]:
        check(f"command:{command['node']}:{command['kind']}", command["returncode"] == 0)
        check(f"source:{command['node']}:{command['kind']}", sha(command["stdin_file"]) == command["stdin_sha256"])
    alignment = json.loads((args.run / "alignment_summary.json").read_text())
    for node in ("node27", "node29"):
        check(f"namespace:{node}", alignment["nodes"][node]["namespace_matches_host"])
        container = json.loads((args.run / node / "container.json").read_text())
        for file in container["files"]:
            if "saved_file" in file:
                check(f"xml_sha:{node}:{file['path']}", sha(file["saved_file"]) == file["sha256"])
        for gpu in (0, 1):
            path = args.run.parent.parent / "basic_scenarios/qwen_bootstrap_r01" / f"worker_{node}_{gpu}.process.pid.json"
            pid = json.loads(path.read_text())
            found = next((p for p in container["processes"] if p["pid"] == pid["pid"]), None)
            check(f"keeper_identity:{node}:{gpu}", found is not None and found["stat"].split(") ", 1)[1].split()[19] == pid["start_time"])
    runtime = json.loads((args.run / "startup_runtime_evidence.json").read_text())
    for probe in runtime["probes"]:
        check(f"startup_summary_sha:{probe['summary']['path']}", sha(probe["summary"]["path"]) == probe["summary"]["sha256"])
        check(f"startup_passed:{probe['summary']['path']}", probe["status"] == "passed")
        for rank in probe["ranks"]:
            check(f"actual_graph_sha:{rank['graph_file']['path']}", sha(rank["graph_file"]["path"]) == rank["graph_file"]["sha256"])
            check(f"non_gdr_channels:{rank['graph_file']['path']}", rank["channel_connections"] == 64 and rank["gdr_channel_connections"] == 0 and rank["channel_mode_observed"] == "non-GDR")
            gpu = next(x for x in rank["graph"] if x["type"] == "gpu")
            expected = "3" if "empty" in probe["summary"]["path"] else "1"
            check(f"numa_actual:{rank['graph_file']['path']}", gpu["cpu"]["numaid"] == (expected if rank["rank"] in (1, 3) else "0"))
    for path in Path(__file__).parent.glob("*.py"):
        compile(path.read_text(), str(path), "exec")
        check(f"python_syntax:{path.name}", True)
    output = {"status": "passed" if all(c["passed"] for c in checks) else "failed", "checks": checks, "checks_count": len(checks), "gpu_operations": 0}
    if args.out.exists():
        raise FileExistsError(args.out)
    args.out.write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps({"status": output["status"], "checks": len(checks), "failed": [c for c in checks if not c["passed"]]}))
    if output["status"] != "passed":
        raise SystemExit(1)

if __name__ == "__main__":
    main()
