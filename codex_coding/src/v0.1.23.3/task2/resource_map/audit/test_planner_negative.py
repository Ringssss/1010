"""CPU-only planning admission cases built without the map builder."""

import copy
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
spec = importlib.util.spec_from_file_location("audited_map", ROOT / "main_lib/fluidckpt/topology/resource_map.py")
rm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rm)


def fixture():
    resources = {}
    for i in range(2):
        resources[f"g{i}"] = {"id": f"g{i}", "kind": "gpu", "node": "n",
                              "bdf": f"0000:0{i + 1}:00.0", "logical_gpu": i,
                              "pci_chain_leaf_to_root": [f"g{i}", "bridge"]}
    resources["bridge"] = {"id": "bridge", "kind": "pci_bridge", "node": "n", "bdf": "0000:00:01.1"}
    path = {"src": "g0", "dst": "g1", "src_tier": "HBM", "dst_tier": "HBM"}
    current = {"id": "current", "primitive": "copy", "runtime_config_id": "cfg",
               "runtime_config_fingerprint": "fixture",
               "directed_paths": [path], "rate_Bps": 100.0, "measured_at": 1000,
               "status": "verified_current", "source_refs": [{"path": "fixture", "sha256": "fixture"}],
               "timing": {"planning_compatible": True}, "admission": {"current_profile_eligible": True},
               "availability_context_id": "current"}
    availability = {rid: {"sampled_at": 1000, "owner_sampled_at": 1000, "boot_id": "boot",
                           "owner_inventory_complete": True, "exclusive": True,
                           "external_active_owners": [], "unknown_owners": [], "authorized_budget_bytes": 10,
                           "budget_authorization_ref": "synthetic grant", "free_bytes": 100,
                           "permitted_for_task": True} for rid in ("g0", "g1")}
    mapping = {"schema_version": 1, "physical": {"nodes": {"n": {"boot_id": "boot"}}, "resources": resources},
               "runtime_configs": {"cfg": {"fingerprint": "fixture", "runtime_observed": True, "observed_resource_ids": ["g0", "g1"]}},
               "availability": {"context_id": "current", "resources": availability},
               "measurements": [current], "planning_readiness": {}}
    request = {"resource_ids": ["g0", "g1"], "runtime_config_id": "cfg", "primitive": "copy",
               "directed_paths": [path], "bytes_budget_by_resource": {"g0": 10, "g1": 10}}
    return mapping, request


class PlannerNegativeTests(unittest.TestCase):
    def setUp(self):
        self.mapping, self.request = fixture()

    def check(self):
        return rm.planner_readiness(self.mapping, self.request, now=1000, max_age_s=30)

    def rejects(self):
        self.assertFalse(self.check()["ready"])
        with self.assertRaises(rm.PlanningNotReady):
            rm.planning_input(self.mapping, self.request, now=1000, max_age_s=30)

    def test_explicit_current_positive(self):
        self.assertTrue(self.check()["ready"], self.check())

    def test_active_external_owner_with_headroom(self):
        self.mapping["availability"]["resources"]["g0"]["external_active_owners"] = [{"pid": 10}]
        self.rejects()

    def test_allow_shared_boolean_cannot_clear_owner_gate(self):
        self.mapping["availability"]["resources"]["g0"]["external_active_owners"] = [{"pid": 10}]
        self.request["allow_shared"] = True
        self.rejects()

    def test_runtime_scope_does_not_expand_by_boolean(self):
        self.mapping["runtime_configs"]["cfg"]["observed_resource_ids"] = ["g0"]
        self.rejects()

    def test_current_rate_must_bind_exact_runtime_fingerprint(self):
        self.mapping["measurements"][0]["runtime_config_fingerprint"] = "different"
        self.rejects()

    def test_unreachable_control_or_unconfirmed_cleanup_blocks(self):
        for status in ({"control_reachable": False, "own_probe_cleanup_confirmed": True},
                       {"control_reachable": True, "own_probe_cleanup_confirmed": False}):
            self.mapping, self.request = fixture()
            self.mapping["current_operational_status"] = {"nodes": {"n": status}}
            self.rejects()

    def test_duplicate_gpu_ordinal_is_invalid(self):
        self.mapping["physical"]["resources"]["g1"]["logical_gpu"] = 0
        self.rejects()

    def test_cross_node_shared_ancestor_is_invalid(self):
        self.mapping["physical"]["resources"]["bridge"]["node"] = "other"
        self.rejects()

    def test_fabric_capacity_cannot_be_created_without_evidence(self):
        self.mapping["physical"]["fabric"] = {"internal_topology": None, "capacity_Bps": 10**12,
                                              "sharing_interference": None}
        self.rejects()

    def test_boolean_resource_capacity_is_not_numeric_measurement(self):
        self.mapping["physical"]["resources"]["bridge"].update(capacity_Bps=True, capacity_evidence_ref="fixture")
        self.rejects()

    def test_free_capacity_does_not_authorize_budget(self):
        self.mapping["availability"]["resources"]["g0"]["authorized_budget_bytes"] = None
        self.rejects()

    def test_stale_owner_and_boot_mismatch(self):
        for field, value in (("owner_sampled_at", 1), ("boot_id", "other")):
            with self.subTest(field=field):
                self.mapping, self.request = fixture()
                self.mapping["availability"]["resources"]["g0"][field] = value
                self.rejects()

    def test_nominal_rate_is_not_measured_effective(self):
        self.mapping["measurements"] = []
        self.mapping["physical"]["resources"]["g0"]["nominal_link_Bps"] = 10**12
        self.rejects()

    def test_bad_profiles_cannot_admit(self):
        for field, value in (("status", "historical_interfered"), ("rate_Bps", float("nan")),
                             ("availability_context_id", "historical"), ("measured_at", 1)):
            with self.subTest(field=field):
                self.mapping, self.request = fixture()
                self.mapping["measurements"][0][field] = value
                self.rejects()

    def test_planning_input_drops_unadmitted_matching_evidence(self):
        historical = copy.deepcopy(self.mapping["measurements"][0])
        historical.update(id="historical", status="historical_interfered", rate_Bps=10**15)
        self.mapping["measurements"].append(historical)
        result = rm.planning_input(self.mapping, self.request, now=1000, max_age_s=30)
        evidence = result["directed_path_evidence"][0]
        self.assertEqual([r["id"] for r in evidence], ["current"])

    def test_multi_path_aggregate_cannot_be_reused_per_path(self):
        reverse = {"src": "g1", "dst": "g0", "src_tier": "HBM", "dst_tier": "HBM"}
        self.mapping["measurements"][0]["directed_paths"].append(reverse)
        self.request["directed_paths"].append(reverse)
        self.rejects()

    def test_shared_ancestor_is_returned_once(self):
        common = rm.shared_ancestors(self.mapping, "g0", "g1")
        self.assertEqual([r["id"] for r in common], ["bridge"])
        self.assertNotIn("capacity_Bps", common[0])

    def test_unknown_and_duplicate_resource_scope(self):
        for ids in (["g0", "other"], ["g0", "g0"]):
            self.request["resource_ids"] = ids
            self.rejects()


if __name__ == "__main__":
    unittest.main(verbosity=2)
