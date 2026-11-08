"""CPU negative checks for local XML mapping and protection contracts."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import common
import run_local

XML = b'<system version="2"><cpu numaid="3" affinity="ffff"><pci busid="0000:30:01.1"><pci busid="0000:36:00.0"><gpu gdr="0"/></pci></pci></cpu></system>'
DUMP = XML.replace(b'<gpu gdr="0"/>', b'<gpu gdr="0" rank="1" dev="1"/>')


def worker(gpu=0):
    return {"worker": gpu, "node": "node27", "gpu": gpu, "numa": gpu, "pid": 100+gpu,
            "epoch": "epoch", "session": "session", "replicas": [{"id": "full", "n": 100,
            "leases": 0, "meta": {"layout": "captured"}, "index": {"tensor": [0,100]}, "state": "VALID"}],
            "transactions": [], "transaction_phases": {}, "used_bytes": {"dram": 100},
            "hbm_allocated_bytes": 0, "hbm_reserved_bytes": 0, "staging_bytes": 64 << 20,
            "headroom_bytes": {"hbm": 6 << 30}}


class Contracts(unittest.TestCase):
    def test_mapping_positive(self):
        self.assertTrue(common.mapping_check(XML, DUMP, "0000:36:00.0", 1)["passed"])

    def test_wrong_numa(self):
        self.assertFalse(common.mapping_check(XML, DUMP.replace(b'numaid="3"', b'numaid="1"'), "0000:36:00.0", 1)["passed"])

    def test_wrong_ancestor(self):
        self.assertFalse(common.mapping_check(XML, DUMP.replace(b"0000:30:01.1", b"0000:70:01.1"), "0000:36:00.0", 1)["passed"])

    def test_wrong_rank_dev(self):
        self.assertFalse(common.mapping_check(XML, DUMP.replace(b'rank="1"', b'rank="0"'), "0000:36:00.0", 1)["passed"])
        self.assertFalse(common.mapping_check(XML, DUMP.replace(b'dev="1"', b'dev="0"'), "0000:36:00.0", 1)["passed"])

    def test_gdr_policy(self):
        self.assertFalse(common.mapping_check(XML, DUMP.replace(b'gdr="0"', b'gdr="1"'), "0000:36:00.0", 1)["passed"])

    def test_missing_selected_gpu(self):
        self.assertFalse(common.mapping_check(XML, DUMP, "0000:09:00.0", 0)["passed"])

    def test_duplicate_gpu_rejected(self):
        with self.assertRaises(ValueError):
            common.gpu_paths(DUMP.replace(b"</pci></pci>", b'<gpu gdr="0"/></pci></pci>'))

    def test_catalog_includes_lease_metadata_epoch(self):
        old = [worker(0), worker(1)]
        for field, value in (("leases", 1), ("meta", {"layout": "wrong"}), ("index", {"tensor": [1,99]})):
            new = copy.deepcopy(old)
            new[0]["replicas"][0][field] = value
            self.assertNotEqual(common.catalog_hash(old), common.catalog_hash(new))
        new = copy.deepcopy(old)
        new[0]["epoch"] = "restarted"
        self.assertNotEqual(common.catalog_hash(old), common.catalog_hash(new))

    def test_free_recovery_original_one_sided_gate(self):
        old, new = [worker(0), worker(1)], [worker(0), worker(1)]
        new[0]["headroom_bytes"]["hbm"] -= 128 << 20
        self.assertTrue(common.free_check(old, new)["passed"])
        new[0]["headroom_bytes"]["hbm"] -= 1
        self.assertFalse(common.free_check(old, new)["passed"])
        new[0]["headroom_bytes"]["hbm"] = 7 << 30
        self.assertTrue(common.free_check(old, new)["passed"])

    def test_released_tombstone_admitted_and_state_preserved(self):
        base = {"id": "base", "state": "VALID", "leases": 0}
        history = {"id": "retired", "state": "RELEASED", "leases": 0}
        self.assertEqual(common.catalog_lease_errors([base, history], [base]), [])
        old = [worker(0), worker(1)]
        old[0]["replicas"].append(history)
        new = copy.deepcopy(old)
        new[0]["replicas"][-1]["state"] = "VALID"
        self.assertNotEqual(common.catalog_hash(old), common.catalog_hash(new))

    def test_base_nonvalid_or_any_lease_rejected(self):
        base = {"id": "base", "state": "RELEASED", "leases": 0}
        self.assertTrue(common.catalog_lease_errors([base], [base]))
        base["state"] = "VALID"
        base["leases"] = 1
        self.assertTrue(common.catalog_lease_errors([base], [base]))
        base["leases"] = 0
        history = {"id": "retired", "state": "RELEASED", "leases": 1}
        self.assertTrue(common.catalog_lease_errors([base, history], [base]))

    def test_output_guard(self):
        with self.assertRaises(ValueError):
            common.owned(common.RESULT_ROOT / "xml_owned_r01")
        with self.assertRaises(ValueError):
            common.owned(common.RESULT_ROOT / "local_xml_x" / "local_xml_nested")

    def test_original_mapping_path(self):
        self.assertIn("/opt/dtk/rccl/lib/topo_mapping_default.xml", common.ORIGINAL_FILES)

    def test_actual_transport_requires_owned_log_input_and_local_group(self):
        log = b'host:123:456 [1] NCCL INFO Loading topology file /owned/input.xml\nhost:123:456 [1] NCCL INFO comm 0x1 rank 1 nRanks 2 nNodes 1 localRanks 2 localRank 1 MNNVL 0\nhost:123:456 [1] NCCL INFO Channel 00 : 0 -> 1 via P2P/direct pointer\n'
        self.assertTrue(run_local.log_evidence(log, 123, 1, Path("/owned/input.xml"))["passed"])
        self.assertFalse(run_local.log_evidence(log, 999, 1, Path("/owned/input.xml"))["passed"])
        self.assertFalse(run_local.log_evidence(log.replace(b"via P2P", b"disabled P2P"), 123, 1, Path("/owned/input.xml"))["passed"])
        self.assertFalse(run_local.log_evidence(log + b"host:123:456 [1] NCCL INFO Loading topology mapping file /default.xml\n", 123, 1, Path("/owned/input.xml"))["passed"])

    def test_specs_local_and_unforced(self):
        out = common.RESULT_ROOT / "local_xml_cpu_mock"
        rows = {}
        def capture(path, value):
            rows[str(path)] = value
        with patch.object(run_local, "write", capture), patch.object(run_local, "digest", return_value="sha"):
            spec_rows = run_local.specs({"env": {}}, out, SimpleNamespace(port=34300, timeout=120), Path("/owned/local.xml"))
        self.assertEqual(len(spec_rows), 2)
        for i, spec in enumerate(rows.values()):
            self.assertEqual(spec["command"][0], "python3")
            self.assertIn("tcp://127.0.0.1:34300", spec["command"])
            self.assertEqual(spec["env"]["NCCL_TOPO_DUMP_FILE_RANK"], str(i))
            self.assertEqual(spec["env"]["GLOO_SOCKET_IFNAME"], "lo")
            self.assertNotIn("NCCL_NET_GDR_LEVEL", spec["env"])
            self.assertNotIn("NCCL_P2P_DISABLE", spec["env"])
            self.assertNotIn("NCCL_IB_HCA", spec["env"])

    def test_real_xml_node27_only_cpu_validation(self):
        path, manifest, value = common.input_xml(common.RESULT_ROOT / "xml_owned_r01")
        self.assertEqual(value["node"], "node27")
        self.assertEqual(len(common.gpu_paths(path.read_bytes())), 8)


if __name__ == "__main__":
    unittest.main(verbosity=2)
