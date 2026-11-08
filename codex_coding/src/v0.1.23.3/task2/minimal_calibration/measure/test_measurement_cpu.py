"""CPU checks for workpoint rejection and full-byte chunk-boundary verification."""
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest

MODULE_PATH = Path(__file__).with_name("measurement_driver.py")
SPEC = importlib.util.spec_from_file_location("measurement_driver", MODULE_PATH)
DRIVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DRIVER)


def configuration():
    return {"node": "node30", "source_node": "node28", "source_numa": 0,
            "source_resource_id": "node28/numa/0", "target_resource_id": "node30/pci/0000:85:00.0",
            "source_gpu_resource_id": "node28/pci/0000:09:00.0", "bdf": "0000:85:00.0",
            "primitive": "remote_numa_h2d_rccl_broadcast", "payload_bytes": 64 << 20,
            "load_scope": "guarded_candidate_scope", "owner_refs": ["current_snapshot.json"],
            "guard_before_ref": "guard.json", "master": "tcp://12.11.3.28:29910", "rank": 1,
            "xml_manifest": "owned_manifest.json"}


class MeasurementContracts(unittest.TestCase):
    def test_finite_workpoint(self):
        self.assertTrue(DRIVER.validate(configuration()))
        smaller_second_point = configuration()
        smaller_second_point["payload_bytes"] = 256 << 20
        self.assertTrue(DRIVER.validate(smaller_second_point))
        bad = configuration()
        bad["payload_bytes"] = 2 << 30
        with self.assertRaises(ValueError):
            DRIVER.validate(bad)
        deferred = configuration()
        deferred["payload_bytes"] = 512 << 20
        with self.assertRaises(ValueError):
            DRIVER.validate(deferred)

    def test_source_id_binds_numa(self):
        bad = configuration()
        bad["source_numa"] = 2
        with self.assertRaises(ValueError):
            DRIVER.validate(bad)

    def test_rejects_unqualified_scope(self):
        for key, value in (("node", "node29"), ("load_scope", "exclusive"), ("owner_refs", []),
                           ("flow_count", 2), ("gpu_guard_bytes", 1)):
            with self.subTest(key=key):
                bad = configuration()
                bad[key] = value
                with self.assertRaises(ValueError):
                    DRIVER.validate(bad)

    def test_full_chunk_boundary_SHA_and_corruption(self):
        import numpy as np
        import torch
        torch.set_num_threads(1)
        size = DRIVER.CHUNK + 17
        tensor = torch.empty(size, dtype=torch.uint8, device="cpu")
        expected = hashlib.sha256(memoryview((np.arange(size, dtype=np.uint32) % 251).astype(np.uint8))).hexdigest()
        self.assertEqual(DRIVER.fill_pattern(tensor, size), expected)
        self.assertEqual(DRIVER.verify(tensor), (expected, size))
        tensor[-1] ^= 1
        actual, verified = DRIVER.verify(tensor)
        self.assertEqual(verified, size)
        self.assertNotEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
