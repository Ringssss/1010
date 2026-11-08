"""Build process-scoped RCCL seeds from frozen physical sysfs evidence."""
from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET


def cpu_affinity(cpulist: str) -> str:
    mask = 0
    for group in cpulist.split(","):
        limits = list(map(int, group.split("-")))
        for cpu in range(limits[0], limits[-1] + 1):
            mask |= 1 << cpu
    words = max(4, (mask.bit_length() + 31) // 32)
    return ",".join(f"{(mask >> (32 * i)) & 0xffffffff:08x}" for i in reversed(range(words)))


def physical_xml(host: dict, *, gdr: int = 0) -> tuple[bytes, dict]:
    """Return a full physical seed and a separate declaration policy.

    Runtime ranks, GPU ordinals, plugin net IDs, and GUIDs are deliberately
    discovered by the fresh communicator, never copied from another rank/node.
    """
    if gdr != 0:
        raise ValueError("only conservative gdr=0 is admitted by this builder")
    root = ET.Element("system", version="2")
    cpus, pci = {}, {}
    for name, row in sorted(host["numa"].items(), key=lambda x: int(x[0][4:])):
        numa = str(int(name[4:]))
        cpus[numa] = ET.SubElement(root, "cpu", numaid=numa, affinity=cpu_affinity(row["cpulist"]))
    unknown, devices = [], []
    for kind, rows in (("gpu", host["kfd"]), ("hca", host["hca"])):
        for row in sorted(rows, key=lambda x: x["pci_chain"][0]["bdf"]):
            chain = row["pci_chain"]
            numa = chain[0]["numa_node"]
            if numa not in cpus:
                raise ValueError(f"unknown NUMA for {chain[0]['bdf']}")
            parent = cpus[numa]
            for entry in reversed(chain):
                bdf = entry["bdf"]
                attrs = {"busid": bdf}
                for key in ("class", "vendor", "device"):
                    value = entry.get(key)
                    if isinstance(value, str):
                        attrs[key] = value
                    else:
                        unknown.append({"bdf": bdf, "field": key, "state": "unknown", "xml": "omitted"})
                for raw, dest in (("current_link_speed", "link_speed"), ("current_link_width", "link_width")):
                    value = entry.get(raw)
                    if isinstance(value, str) and value and value != "Unknown":
                        attrs[dest] = value
                    else:
                        unknown.append({"bdf": bdf, "field": raw, "state": "unknown", "xml": "omitted; runtime may apply its fallback"})
                if bdf in pci:
                    existing, expected_parent = pci[bdf]
                    if existing.attrib != attrs or expected_parent is not parent:
                        raise ValueError(f"conflicting physical PCI evidence: {bdf}")
                    parent = existing
                else:
                    element = ET.SubElement(parent, "pci", attrs)
                    pci[bdf] = (element, parent)
                    parent = element
            if kind == "gpu":
                ET.SubElement(parent, "gpu", gdr="0")
            else:
                nic = ET.SubElement(parent, "nic")
                for port, values in sorted(row["ports"].items()):
                    attrs = {"name": row["name"], "port": str(port), "gdr": "0"}
                    match = re.match(r"([\d.]+) Gb/sec", str(values.get("rate", "")))
                    if match:
                        attrs["speed"] = str(int(float(match[1]) * 1000))
                    else:
                        unknown.append({"hca": row["name"], "port": port, "field": "rate", "state": "unknown", "xml": "omitted"})
                    ET.SubElement(nic, "net", attrs)
            devices.append({"kind": kind, "bdf": chain[0]["bdf"], "numa": numa,
                            "pci_ancestors_root_to_leaf": [p["bdf"] for p in reversed(chain)]})
    ET.indent(root, space="  ")
    raw = ET.tostring(root, encoding="utf-8") + b"\n"
    policy = {"schema_version": "fluidckpt.rccl_physical_seed.v1", "scope": "process-scoped candidate input",
              "runtime_accepted": False, "runtime_observed": False, "performance_accepted": False,
              "gdr_declaration": {"gpu": 0, "net": 0, "reason": "conservative experimental policy; not a physical capability finding"},
              "runtime_filled_fields": ["gpu.dev", "gpu.rank", "gpu.sm", "gpu.gcn", "gpu.arch", "net.dev", "net.guid", "net.latency", "net.maxconn", "cpu.arch/vendor/familyid/modelid", "GPU interconnects"],
              "pci_link_policy": "current negotiated sysfs speed/width; no measured bandwidth claim",
              "unknown": unknown, "physical_devices": devices, "sha256": hashlib.sha256(raw).hexdigest()}
    return raw, policy


def validate_physical_xml(raw: bytes, host: dict, container: dict) -> dict:
    """Independently compare every emitted leaf/ancestor against raw evidence."""
    root = ET.fromstring(raw)
    checks = {"system_version": root.tag == "system" and root.get("version") == "2"}
    namespace = {x["resolved"]: x for x in container["namespace_sysfs"]["devices"]}
    found, parents = {}, {child: parent for parent in root.iter() for child in parent}
    for item in root.iter("pci"):
        if item.get("busid") in found:
            raise ValueError("duplicate PCI identity")
        found[item.get("busid")] = item
    checks["8_gpu_4_hca"] = len(root.findall(".//gpu")) == 8 and len(root.findall(".//nic")) == 4
    checks["conservative_gdr_policy"] = all(e.get("gdr") == "0" for e in [*root.iter("gpu"), *root.iter("net")])
    checks["no_dynamic_identity_claims"] = all(not ({"rank", "dev", "guid"} & set(e.attrib)) for e in root.iter())
    checks["no_interconnect_invention"] = not root.findall(".//xgmi") and not root.findall(".//nvlink")
    expected_pci = set()
    for kind, rows in (("gpu", host["kfd"]), ("hca", host["hca"])):
        for row in rows:
            chain = row["pci_chain"]
            bdf, expected_numa = chain[0]["bdf"], chain[0]["numa_node"]
            expected_pci.update(e["bdf"] for e in chain)
            current, ancestry = found.get(bdf), []
            while current is not None and current.tag == "pci":
                ancestry.append(current.get("busid"))
                current = parents.get(current)
            checks[bdf + ":full_ancestry"] = ancestry == [e["bdf"] for e in chain]
            checks[bdf + ":numa"] = current is not None and current.get("numaid") == expected_numa
            checks[bdf + ":cpu_affinity"] = current is not None and current.get("affinity") == cpu_affinity(chain[0]["local_cpulist"])
            ns = namespace.get(chain[0]["sysfs"])
            checks[bdf + ":namespace"] = ns is not None and all(ns.get(k) == chain[0].get(k) for k in ("numa_node", "local_cpulist"))
            leaf = found.get(bdf)
            expected_tag = "gpu" if kind == "gpu" else "nic"
            checks[bdf + ":leaf_type"] = leaf is not None and len(leaf.findall(expected_tag)) == 1 and not leaf.findall("nic" if kind == "gpu" else "gpu")
            for entry in chain:
                element = found.get(entry["bdf"])
                for raw_key, xml_key in (("class", "class"), ("vendor", "vendor"), ("device", "device"), ("current_link_speed", "link_speed"), ("current_link_width", "link_width")):
                    expected = entry.get(raw_key)
                    expected = expected if isinstance(expected, str) and expected != "Unknown" else None
                    checks[entry["bdf"] + ":" + xml_key] = element is not None and element.get(xml_key) == expected
            if kind == "hca" and found.get(bdf) is not None:
                nets = found[bdf].findall("nic/net")
                checks[bdf + ":ports"] = {(e.get("name"), e.get("port")) for e in nets} == {(row["name"], str(p)) for p in row["ports"]}
                checks[bdf + ":unique_ports"] = len(nets) == len(row["ports"])
                for port, values in row["ports"].items():
                    match = re.match(r"([\d.]+) Gb/sec", str(values.get("rate", "")))
                    expected = str(int(float(match[1]) * 1000)) if match else None
                    matches = [e for e in nets if e.get("port") == str(port)]
                    checks[bdf + ":port_" + str(port) + ":speed"] = len(matches) == 1 and matches[0].get("speed") == expected
    checks["all_and_only_physical_pci"] = set(found) == expected_pci
    checks["all_cpu_numa"] = {e.get("numaid") for e in root.findall("cpu")} == {k[4:] for k in host["numa"]}
    checks["unique_cpu_numa"] = len(root.findall("cpu")) == len(host["numa"])
    return {"passed": all(checks.values()), "checks": checks, "failed": [k for k, v in checks.items() if not v], "runtime_accepted": False, "performance_accepted": False}
