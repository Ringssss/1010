"""Read already completed parent-owned startup probes; record actual graph evidence."""
import argparse
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET
from analyze_provenance import mappings

def identity(path):
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", type=Path, action="append", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    output = {"read_only": True, "gpu_operations_by_forensics": 0, "probes": []}
    for probe in args.probe:
        summary_path = probe / "postexit" / "summary.json"
        summary = json.loads(summary_path.read_text())
        record = {"summary": identity(summary_path), "status": summary["status"], "ranks": []}
        for rank in range(4):
            binding = next(b for b in summary["binding"] if b["rank"] == rank)
            xml_path = probe / "postexit" / f"rank_{rank}.topology.xml"
            graph = mappings(ET.parse(xml_path).getroot())
            channels = binding["all_channel_transport_lines"]
            gdr = [line for line in channels if "/GDRDMA" in line]
            evidence = {"rank": rank, "graph_file": identity(xml_path), "graph": graph, "channel_connections": len(channels), "gdr_channel_connections": len(gdr), "channel_mode_observed": "GDR" if gdr and len(gdr) == len(channels) else "non-GDR" if channels and not gdr else "mixed_or_unknown", "channel_lines": channels, "hca_gdr_lines": binding["gdr_decision_lines"], "actual_hca_ports": binding["actual_hca_ports"], "topology_lines": binding["topology_loading_lines"] if "topology_loading_lines" in binding else [], "upstream_summary_flags": {"gdr_enabled_observed": binding["gdr_enabled_observed"], "gdr_disabled_observed": binding["gdr_disabled_observed"]}}
            for key, value in binding.items():
                if isinstance(value, list) and all(isinstance(line, str) for line in value):
                    selected = [line for line in value if any(s in line for s in ("topology mapping group", "Loading topology file", "NCCL_TOPO_FILE set", "Setting affinity"))]
                    if selected:
                        evidence.setdefault("topology_evidence_by_key", {})[key] = selected
            record["ranks"].append(evidence)
        output["probes"].append(record)
    if args.out.exists():
        raise FileExistsError(args.out)
    args.out.write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps({"out": str(args.out), "ranks": [{"probe": p["summary"]["path"], "rank": r["rank"], "channel_mode": r["channel_mode_observed"]} for p in output["probes"] for r in p["ranks"]]}))

if __name__ == "__main__":
    main()
