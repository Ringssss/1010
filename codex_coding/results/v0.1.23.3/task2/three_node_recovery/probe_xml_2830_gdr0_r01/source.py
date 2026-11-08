"""Reuse the physical XML builder for arbitrary admitted node evidence pairs (CPU only)."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT / "main_lib"))
from fluidckpt.topology.rccl_xml import physical_xml, validate_physical_xml


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node-evidence", nargs=3, action="append", metavar=("NODE", "HOST_JSON", "CONTAINER_JSON"), required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    root = ROOT / "codex_coding/results/v0.1.23.3/task2/three_node_recovery"
    args.out = args.out.resolve()
    if args.out.parent != root or not args.out.name.startswith("probe_xml_"):
        parser.error("require a new versioned three_node_recovery/probe_xml_* directory")
    nodes = [row[0] for row in args.node_evidence]
    if len(nodes) != len(set(nodes)) or any(n not in ("node27", "node28", "node30") for n in nodes):
        parser.error("require unique node27/28/30 evidence; node29 excluded")
    args.out.mkdir(parents=True, exist_ok=False)
    manifests = []
    for node, host_path, container_path in args.node_evidence:
        paths = [Path(host_path).resolve(), Path(container_path).resolve()]
        host, container = [json.loads(path.read_text()) for path in paths]
        if not host.get("hostname"):
            raise ValueError("host evidence hostname missing")
        raw, policy = physical_xml(host)
        validation = validate_physical_xml(raw, host, container)
        if not validation["passed"]:
            raise ValueError(validation["failed"])
        destination = args.out / f"{node}.physical.gdr0.xml"
        destination.write_bytes(raw)
        policy.update(node=node, hostname=host["hostname"], input_file=str(destination),
                      source_refs=[{"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in paths],
                      validation_file=str(args.out / f"{node}.validation.json"))
        for suffix, value in (("manifest", policy), ("validation", validation)):
            (args.out / f"{node}.{suffix}.json").write_text(json.dumps(value, indent=2) + "\n")
        manifests.append(policy)
    summary = {"schema_version": "fluidckpt.rccl_physical_seed_collection.v1", "nodes": manifests,
               "cpu_structure_passed": True, "runtime_accepted": False, "performance_accepted": False,
               "gpu_operations": 0, "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (args.out / "manifest.json").write_text(json.dumps(summary, indent=2) + "\n")
    (args.out / "source.py").write_bytes(Path(__file__).read_bytes())
    print(json.dumps({"out": str(args.out), "cpu_structure_passed": True, "runtime_accepted": False}))


if __name__ == "__main__":
    main()
