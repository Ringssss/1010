"""Build immutable per-node physical XML candidates without GPU initialization."""
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
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out = args.out.resolve()
    allowed = ROOT / "codex_coding/results/v0.1.23.3/task2/resource_map"
    if args.out.parent != allowed or not args.out.name.startswith("xml_"):
        parser.error("output must be a new versioned resource_map/xml_* directory")
    args.out.mkdir(parents=True, exist_ok=False)
    manifests = []
    for node in ("node27", "node29"):
        paths = [args.provenance / node / (name + ".json") for name in ("host", "container")]
        host, container = [json.loads(p.read_text()) for p in paths]
        raw, policy = physical_xml(host)
        destination = args.out / (node + ".physical.gdr0.xml")
        destination.write_bytes(raw)
        validation = validate_physical_xml(destination.read_bytes(), host, container)
        if not validation["passed"]:
            raise ValueError(validation["failed"])
        policy.update(node=node, hostname=host["hostname"], input_file=str(destination),
                      source_refs=[{"path": str(p.resolve()), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in paths],
                      validation_file=str(args.out / (node + ".validation.json")))
        (args.out / (node + ".manifest.json")).write_text(json.dumps(policy, indent=2) + "\n")
        (args.out / (node + ".validation.json")).write_text(json.dumps(validation, indent=2) + "\n")
        manifests.append(policy)
    summary = {"schema_version": "fluidckpt.rccl_physical_seed_collection.v1", "nodes": manifests,
               "cpu_structure_passed": True, "runtime_accepted": False, "performance_accepted": False,
               "gpu_operations": 0, "sources_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (args.out / "manifest.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({"out": str(args.out), "cpu_structure_passed": True, "runtime_accepted": False}))


if __name__ == "__main__":
    main()
