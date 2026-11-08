"""Compare collected host, container and XML representations without hardware calls."""
import argparse
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

def expand(mask):
    bits = int(mask.replace(",", ""), 16)
    return [i for i in range(bits.bit_length()) if bits & (1 << i)]

def cpulist(value):
    result = []
    for part in value.split(","):
        low, _, high = part.partition("-")
        result.extend(range(int(low), int(high or low) + 1))
    return result

def mappings(root):
    result = []
    def visit(element, cpu=None, pci=()):
        if element.tag == "cpu":
            cpu = element.attrib
        if element.tag == "pci":
            pci = (*pci, element.get("busid"))
        if element.tag in ("gpu", "net"):
            result.append({"type": element.tag, "attributes": element.attrib, "cpu": cpu, "pci_chain_root_to_leaf": pci})
        for child in element:
            visit(child, cpu, pci)
    visit(root)
    return result

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    output = {"schema": "topology_provenance_alignment.v1", "nodes": {}, "unknown": ["Docker inspect cannot distinguish inherited image ENV from an explicit same-value override.", "Static mapping group availability does not prove the exact runtime group selection.", "Empty system seed bypass has static support but requires actual runtime dump verification.", "No vendor source commit or hardware design rationale was found; comments describe supported remapping mechanics.", "RCCL discovery may apply additional vendor topology logic after sysfs; accurate graph is accepted only against actual dumps."]}
    for node in ("node27", "node29"):
        host = json.loads((args.run / node / "host.json").read_text())
        container = json.loads((args.run / node / "container.json").read_text())
        builtin = next(f for f in container["files"] if "built-in-508" in f["path"])
        xml = ET.parse(builtin["saved_file"]).getroot()
        entries = mappings(xml)
        checks = []
        lookup = {k["pci_chain"][0]["bdf"]: k["pci_chain"][0] for k in host["kfd"]}
        lookup.update({h["pci_chain"][0]["bdf"]: h["pci_chain"][0] for h in host["hca"]})
        for dev in container["namespace_sysfs"]["devices"]:
            actual = lookup.get(Path(dev["resolved"]).name)
            checks.append({"path": dev["class_path"], "bdf": Path(dev["resolved"]).name, "same_resolved_path": actual is not None and dev["resolved"] == actual["sysfs"], "same_numa": actual is not None and dev["numa_node"] == actual["numa_node"], "same_cpulist": actual is not None and dev["local_cpulist"] == actual["local_cpulist"]})
        relations = []
        for e in entries:
            bdf = e["pci_chain_root_to_leaf"][-1]
            actual = lookup[bdf]
            item = {"xml": e, "physical": actual, "numa_equal": e["cpu"]["numaid"] == actual["numa_node"], "cpu_mask_equal": expand(e["cpu"]["affinity"]) == cpulist(actual["local_cpulist"])}
            if e["type"] == "gpu":
                kfd = next(k for k in host["kfd"] if k["pci_chain"][0]["bdf"] == bdf)
                item["logical_gpu_identity"] = {"xml_dev": e["attributes"]["dev"], "kfd_node": kfd["node"], "render_minor": kfd["properties"]["drm_render_minor"], "physical_bus": bdf, "note": "Ordinal uses saved HIP inventory corroboration; render minor alone is not a HIP ordinal guarantee."}
                item["physical_chain_leaf_to_root"] = [p["bdf"] for p in kfd["pci_chain"]]
            else:
                hca = next(k for k in host["hca"] if k["pci_chain"][0]["bdf"] == bdf)
                item["physical_chain_leaf_to_root"] = [p["bdf"] for p in hca["pci_chain"]]
            item["xml_ancestors_exist_in_physical_chain"] = all(p in item["physical_chain_leaf_to_root"] for p in e["pci_chain_root_to_leaf"])
            relations.append(item)
        mapping = next(f for f in container["files"] if f["path"].endswith("topo_mapping_default.xml"))
        mapping_xml = ET.parse(mapping["saved_file"]).getroot()
        group = next(g for g in mapping_xml.findall("group") if "HygonGenuine_shca_4_InfiniBand_400-400-400-400" in g.get("name", ""))
        output["nodes"][node] = {"image_topo": host["image"]["env_whitelist"].get("NCCL_TOPO_FILE"), "container_topo": host["container"]["env_whitelist"].get("NCCL_TOPO_FILE"), "keeper_topo": {str(p["pid"]): p["env_whitelist"].get("NCCL_TOPO_FILE") for p in container["processes"]}, "namespace_checks": checks, "namespace_matches_host": all(c["same_resolved_path"] and c["same_numa"] and c["same_cpulist"] for c in checks) and host["numa"] and {k: v["cpulist"] for k, v in host["numa"].items()} == container["namespace_sysfs"]["numa"], "xml_physical_relations": relations, "candidate_vendor_mapping_group": {"name": group.get("name"), "xml": ET.tostring(group, encoding="unicode"), "runtime_selected": None}, "file_sha256": {f["path"]: f.get("sha256") for f in container["files"] if f.get("exists")}}
    destination = args.run / "alignment_summary.json"
    if destination.exists():
        raise FileExistsError(destination)
    destination.write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps({"output": str(destination), "namespace_matches_host": {n: v["namespace_matches_host"] for n, v in output["nodes"].items()}, "xml_mismatches": {n: [r["xml"]["attributes"] for r in v["xml_physical_relations"] if not (r["numa_equal"] and r["cpu_mask_equal"] and r["xml_ancestors_exist_in_physical_chain"])] for n, v in output["nodes"].items()}}))

if __name__ == "__main__":
    main()
