"""CPU checks for finite admission, physical accounting, and controller phases."""
import copy
import importlib.util
from pathlib import Path
import threading
import unittest

from build_contract import ROOT, block_case, physical_subgraph
from run_session import launch_plan, cleanup_owned, execute
from unittest.mock import patch
from fluidckpt.benchmarks.calibrated_recovery import execute_collection, observed_workpoint_score
from fluidckpt.benchmarks.topology_cases import validate_case
from fluidckpt.cluster.control import Cluster, CONTROLLER_CLOCK_DOMAIN
from fluidckpt.executor.chunks import DEFAULT_CHUNK, STAGING_BYTES, execution_chunk
from fluidckpt.topology import resource_map as rm

OLD_TEST = ROOT / "codex_coding/src/v0.1.23.3/task2/resource_map/audit/test_planner_negative.py"
old_spec = importlib.util.spec_from_file_location("old_planner_contract", OLD_TEST)
old_test = importlib.util.module_from_spec(old_spec)
old_spec.loader.exec_module(old_test)


def joint_fixture():
    mapping, request = old_test.fixture()
    request["resource_ids"].append("bridge")
    request["bytes_budget_by_resource"]["bridge"] = 0
    mapping["availability"]["resources"]["bridge"] = copy.deepcopy(mapping["availability"]["resources"]["g0"])
    mapping["runtime_configs"]["cfg"]["observed_resource_ids"].append("bridge")
    reverse = dict(src="g1", dst="g0", src_tier="HBM", dst_tier="HBM")
    request["directed_paths"].append(reverse)
    workpoint = dict(workload_id="two-directions/10B", operation="copy", path_bytes=[10, 10],
        concurrency=2, schedule="simultaneous", resource_path_ids=["forward", "reverse"],
        resource_incidence=[["g0", "g1", "bridge"], ["g0", "g1", "bridge"]],
        bytes_budget_by_resource=copy.deepcopy(request["bytes_budget_by_resource"]))
    request["workpoint"] = workpoint
    refs = [{"path": "CPU fixture", "sha256": "synthetic"}]
    mapping["physical"]["directed_paths"] = {pid: dict(path, resource_ids=["g0", "g1", "bridge"],
        runtime_config_ids=["cfg"], source_refs=refs) for pid, path in zip(("forward", "reverse"), request["directed_paths"])}
    mapping["measurements"][0].update(directed_paths=copy.deepcopy(request["directed_paths"]),
        workpoint=copy.deepcopy(workpoint), per_path_rates_Bps=[50, 50],
        timing=dict(planning_compatible=True, clock_scope="single_controller", elapsed_ns=200_000_000),
        load_scope="CPU fixture only")
    mapping["shared_resource_contracts"] = {rid: dict(resource_id=rid, mode="finite_measured_workpoints",
        measurement_ids=["current"], source_refs=refs) for rid in request["resource_ids"]}
    return mapping, request


class FiniteAdmissionTests(unittest.TestCase):
    def test_legacy_single_path_unchanged(self):
        mapping, request = old_test.fixture()
        self.assertTrue(rm.planner_readiness(mapping, request, now=1000)["ready"])

    def test_joint_is_one_workpoint_without_independent_path_caps(self):
        mapping, request = joint_fixture()
        output = rm.planning_input(mapping, request, now=1000)
        self.assertEqual(output["directed_path_evidence"], [])
        self.assertEqual(len(output["joint_workpoint_evidence"]), 1)
        self.assertEqual(set(output["shared_resource_contracts"]), {"g0", "g1", "bridge"})
        self.assertFalse(any("capacity_Bps" in c for c in output["shared_resource_contracts"].values()))

    def test_exact_workload_and_budget_mutations_reject(self):
        for field, value in (("path_bytes", [9, 10]), ("concurrency", 1), ("schedule", "serial"),
                             ("resource_path_ids", ["reverse", "forward"]),
                             ("resource_incidence", [["g0", "g1"], ["g0", "g1"]])):
            with self.subTest(field=field):
                mapping, request = joint_fixture()
                request["workpoint"][field] = value
                self.assertFalse(rm.planner_readiness(mapping, request, now=1000)["ready"])
        mapping, request = joint_fixture()
        request["bytes_budget_by_resource"]["g0"] = 9
        self.assertFalse(rm.planner_readiness(mapping, request, now=1000)["ready"])

    def test_joint_keeps_fresh_config_owner_gate(self):
        for mutation in ("owner", "age", "config", "shared_contract", "capacity"):
            mapping, request = joint_fixture()
            if mutation == "owner": mapping["availability"]["resources"]["g0"]["exclusive"] = False
            if mutation == "age": mapping["measurements"][0]["measured_at"] = 1
            if mutation == "config": mapping["measurements"][0]["runtime_config_fingerprint"] = "changed"
            if mutation == "shared_contract": mapping["shared_resource_contracts"].pop("bridge")
            if mutation == "capacity": mapping["shared_resource_contracts"]["bridge"]["capacity_Bps"] = 100
            self.assertFalse(rm.planner_readiness(mapping, request, now=1000)["ready"], mutation)

    def test_shared_observation_scores_without_formal_admission(self):
        mapping, request = joint_fixture()
        mapping["availability"]["resources"]["g0"]["exclusive"] = False
        row = mapping["measurements"][0]
        row["status"], row["admission"]["current_profile_eligible"] = "observed_shared_load", False
        score = observed_workpoint_score(mapping, request, now=1000)
        self.assertEqual(score["observed_collection_window_s"], 0.2)
        self.assertFalse(score["formal_profile_admitted"])
        self.assertFalse(score["planner_readiness"]["ready"])
        self.assertIsNone(score["solver_capacity_profile"])

    def test_observation_cannot_subtract_remote_domains(self):
        mapping, request = joint_fixture()
        mapping["measurements"][0]["timing"]["clock_scope"] = "mixed_remote_monotonic"
        with self.assertRaises(ValueError): observed_workpoint_score(mapping, request, now=1000)


class ProtocolTests(unittest.TestCase):
    def test_real_subgraph_remains_nonready(self):
        mapping = physical_subgraph()
        self.assertTrue(rm.validate_resource_map(mapping)["passed"])
        self.assertFalse(rm.planner_readiness(mapping)["performance_ready"])
        self.assertFalse(mapping["runtime_configs"]["candidate_gpu0_gpu4"].get("application_executor_qualified", False))

    def test_physical_copy_and_fixed_staging_budget(self):
        for mib in (64, 256):
            case, protocol = block_case(mib << 20, DEFAULT_CHUNK)
            self.assertFalse(protocol["prediction_ready"])
            self.assertEqual(case["budget"]["staging_bytes"], 2*STAGING_BYTES)
            ledgers = [row["ledger"] for row in protocol["layouts"].values()]
            self.assertEqual(ledgers[0]["allocated_hbm_bytes"], ledgers[1]["allocated_hbm_bytes"])
            for row in protocol["layouts"].values():
                self.assertEqual(sum(row["ledger"]["peak_hbm_by_location"].values()), row["ledger"]["peak_hbm_bytes"])
                for plan in row["plans"].values():
                    self.assertEqual(execution_chunk(plan), DEFAULT_CHUNK)
                    self.assertNotIn("estimated_transfer_s", plan)

    def test_prepublication_exception_only_for_declared_synthetic_replica_targets(self):
        case, protocol = block_case(64 << 20, DEFAULT_CHUNK)
        self.assertTrue(validate_case(case)["passed"])
        self.assertTrue(protocol["no_solver_delta"])
        self.assertEqual(protocol["strong_nearest_target_decision_signature"],
                         protocol["layouts"]["target_local_distributed"]["decision_signature"])
        for kind in (None, "model_weights", "packed_weight_snapshot"):
            changed = copy.deepcopy(case)
            changed["collection_kind"] = kind
            with self.assertRaises(ValueError): validate_case(changed)
        changed = copy.deepcopy(case)
        changed["restore_scenarios"][0]["targets"][0]["mode"] = "live"
        with self.assertRaises(ValueError): validate_case(changed)


class ControllerTests(unittest.TestCase):
    def plan(self):
        target = dict(key="t", identity="i", tp=0, worker=0, off=0, n=64, mode="replica")
        op = dict(op_id=0, target="t", identity="i", tp=0, off=0, n=64, src_w=0, dst_w=0,
                  source_offset=0, replica_id="r")
        return dict(targets=[target], ops=[op], executor="serial", chunk_bytes=DEFAULT_CHUNK)

    def test_existing_transfer_phase_order_and_timing(self):
        cluster = Cluster.__new__(Cluster)
        cluster.lock, cluster.active = threading.RLock(), {}
        cluster.executor, cluster.control_mode, cluster.endpoints = "serial", "legacy", ["CPU"]
        events = []
        cluster.inventory = lambda **kw: ([dict(worker=0, epoch="e")], [])
        def calls(operation, bodies, telemetry=None):
            events.append(operation)
            return [dict(records=[dict(op_id=0, bytes=64, role="target")])] if operation == "execute" else [{}]
        cluster.call_all = calls
        cluster.verify = lambda tx: events.append("verify") or dict(passed=True, checked_bytes=64)
        cluster.finish = lambda tx: events.append("finish")
        result = cluster.transfer(self.plan(), verify_before_ready=True)
        self.assertEqual(events, ["prepare", "execute", "verify", "complete", "finish"])
        timing = result["controller_timing"]
        self.assertEqual(timing["clock_domain"], CONTROLLER_CLOCK_DOMAIN)
        self.assertLessEqual(timing["load_begin_ns"], timing["all_targets_filled_ns"])
        self.assertLessEqual(timing["all_targets_filled_ns"], timing["all_targets_published_ns"])

    def test_replica_measures_filled_and_verified_publish_without_model_claim(self):
        events = []
        class Controller:
            def transfer(self, plan, **kw):
                events.append(kw)
                return dict(tx="CPU", verification=dict(passed=True, checked_bytes=64),
                    controller_timing=dict(clock_domain="fixture", load_begin_ns=150,
                                           all_targets_filled_ns=200, all_targets_published_ns=300))
            def finish(self, tx): events.append("finish")
        stamps = iter([100, 400])
        result = execute_collection(Controller(), self.plan(), collection_kind="synthetic_blocks",
            preparation_cost_s=0.7, clock_domain="fixture", clock_ns=lambda: next(stamps))
        self.assertEqual(events, [dict(verify_before_ready=True, retain=True), "finish"])
        self.assertEqual(result["block_buffers_filled_s"], 50/1e9)
        self.assertEqual(result["published_collection_ready_s"], 150/1e9)
        self.assertFalse(result["model_weights_ready_measured"])
        self.assertEqual(result["verification_position"], "before_replica_publish")

    def test_model_without_engine_binding_rejected_before_transfer(self):
        with self.assertRaises(ValueError):
            execute_collection(None, self.plan(), collection_kind="model_weights",
                               preparation_cost_s=0, clock_domain="fixture")

    def test_larger_prepared_case_is_rejected_before_any_rpc(self):
        with patch("run_session.Cluster") as cluster:
            with self.assertRaises(ValueError): execute(dict(block_sizes_mib=[256]), None)
            cluster.assert_not_called()

    def test_cleanup_failure_continues_only_through_owned_live_ids(self):
        class Controller:
            endpoints = ["CPU"]
            def inventory(self):
                return [dict(replicas=[dict(id="a", state="VALID"), dict(id="b", state="VALID"),
                                      dict(id="released", state="RELEASED")])], []
        called = []
        def retire(ep, operation, body):
            called.append(body["id"])
            if body["id"] == "b": raise RuntimeError("CPU injected failure")
        with patch("run_session.request", side_effect=retire):
            errors = cleanup_owned(Controller(), [(0, "a"), (0, "b"), (0, "released")])
        self.assertEqual(called, ["b", "a"])
        self.assertEqual(len(errors), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
