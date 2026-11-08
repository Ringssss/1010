"""Check audit discrimination between physical placement and bridge compression."""
import unittest

from review import cpus, physical_comparison


class PhysicalEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.host = {"drm": [{"pci_chain": [
            {"bdf": "0000:36:00.0", "numa_node": "3", "local_cpulist": "48-63"},
            {"bdf": "0000:35:00.0"}, {"bdf": "0000:31:00.0"}]}], "hca": []}
        self.node = {"kind": "gpu", "attributes": {"dev": "1"},
                     "cpu": {"numaid": "3", "affinity": "ffff0000,00000000"},
                     "pci": [{"busid": "0000:31:00.0"}, {"busid": "0000:36:00.0"}]}

    def test_bridge_compression_preserves_placement(self):
        result = physical_comparison([self.node], self.host)[0]
        self.assertTrue(result["placement_matches"])
        self.assertFalse(result["exact_chain_matches"])

    def test_vendor_numa_and_affinity_mismatch_is_detected(self):
        self.node["cpu"] = {"numaid": "1", "affinity": "ffff0000"}
        result = physical_comparison([self.node], self.host)[0]
        self.assertTrue(result["ancestry_members_match"])
        self.assertFalse(result["numa_matches"])
        self.assertFalse(result["affinity_matches"])
        self.assertFalse(result["placement_matches"])

    def test_wrong_ancestor_does_not_certify_physical_path(self):
        self.node["pci"][0]["busid"] = "0000:71:00.0"
        result = physical_comparison([self.node], self.host)[0]
        self.assertTrue(result["endpoint_bdf_matches"])
        self.assertFalse(result["ancestry_members_match"])
        self.assertFalse(result["placement_matches"])

    def test_discontiguous_cpu_list(self):
        self.assertEqual(cpus("0-2,8,16-17"), {0, 1, 2, 8, 16, 17})


if __name__ == "__main__":
    unittest.main()
