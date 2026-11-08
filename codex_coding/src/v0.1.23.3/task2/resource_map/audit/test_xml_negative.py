"""Independent CPU-only corruption cases for the physical XML contract."""

import importlib.util
import json
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
spec = importlib.util.spec_from_file_location("audited_xml", ROOT / "main_lib/fluidckpt/topology/rccl_xml.py")
xml = importlib.util.module_from_spec(spec)
spec.loader.exec_module(xml)
PROV = ROOT / "codex_coding/results/v0.1.23.3/task2/resource_map/provenance_r01"


class XmlNegativeTests(unittest.TestCase):
    def setUp(self):
        self.host = json.loads((PROV / "node27/host.json").read_text())
        self.container = json.loads((PROV / "node27/container.json").read_text())
        self.raw, self.policy = xml.physical_xml(self.host)

    def rejected(self, edit):
        root = ET.fromstring(self.raw)
        edit(root)
        result = xml.validate_physical_xml(ET.tostring(root), self.host, self.container)
        self.assertFalse(result["passed"], result.get("failed"))

    def test_both_nodes_static_positive_is_not_runtime_acceptance(self):
        for node in ("node27", "node29"):
            host = json.loads((PROV / node / "host.json").read_text())
            container = json.loads((PROV / node / "container.json").read_text())
            raw, policy = xml.physical_xml(host)
            self.assertTrue(xml.validate_physical_xml(raw, host, container)["passed"])
            self.assertFalse(policy["runtime_accepted"])
            self.assertFalse(policy["performance_accepted"])

    def test_gpu1_wrong_numa(self):
        self.rejected(lambda r: r.find("cpu[@numaid='3']").set("numaid", "1"))

    def test_wrong_cpu_mask(self):
        self.rejected(lambda r: r.find("cpu[@numaid='3']").set("affinity", "0"))

    def test_wrong_physical_ancestor(self):
        self.rejected(lambda r: r.find(".//pci[@busid='0000:31:00.0']").set("busid", "0000:71:00.9"))

    def test_wrong_pcie_width(self):
        self.rejected(lambda r: r.find(".//pci[@busid='0000:36:00.0']").set("link_width", "1"))

    def test_invented_runtime_identities(self):
        for field in ("rank", "dev", "guid"):
            self.rejected(lambda r, field=field: r.find(".//gpu").set(field, "999"))

    def test_invented_fabric_speed(self):
        self.rejected(lambda r: r.find(".//net").set("speed", "999999"))

    def test_unaccepted_gdr_policy(self):
        for tag in ("gpu", "net"):
            self.rejected(lambda r, tag=tag: r.find(".//" + tag).set("gdr", "1"))

    def test_extra_cpu_node(self):
        self.rejected(lambda r: ET.SubElement(r, "cpu", numaid="0", affinity="0000ffff"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
