"""CPU-only protocol, real planning and inherited failure-ownership checks."""
import argparse
from collections import Counter
from concurrent.futures import Future
import copy
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import runtime_support as support
import run_layout_scenarios as layout_module
from run_layout_scenarios import (GIB, PROTOCOL, LayoutScenarioRun, cache_layout,
                                  control_signature, dependency_report, reordered_plan, verify_runtime_order, selected_source_bytes, schedule_config)
from fluidckpt.benchmarks.basic_scenarios import assess_case
from fluidckpt.cluster.planning import restore_plan, validate_plan

CHECK_ROOT = None


def fixture(directory):
    model = {"tp": 2, "sources": [{"identity": f"rank{r}", "tp": r, "n": 32*GIB+4096,
        "meta": {"model_identity": "CPU-fixture", "tensors": []}, "index": {"weight": [0, 32*GIB+4096]}}
        for r in range(2)], "source_ids": ["base0", "base1"], "reference": {"text": "ok"}}
    workers = [{"worker": w, "node": "node27" if w < 2 else "node29", "gpu": w % 2, "numa": 0,
        "session": "CPU-fixture", "epoch": f"epoch{w}", "capabilities": ["local_dram", "local_hbm", "peer", "network"],
        "transactions": [], "staging_bytes": 4096, "budget_bytes": {"hbm": 64*GIB},
        "used_bytes": {"hbm": 0}, "headroom_bytes": {"hbm": 64*GIB},
        "hbm_allocated_bytes": 4096, "hbm_reserved_bytes": 4096} for w in range(4)]
    base = [{"id": f"base{r}", "identity": f"rank{r}", "tp": r, "n": s["n"], "off": 0,
        "tier": "dram", "worker": r, "numa": 0, "generation": 1, "state": "VALID", "epoch": f"epoch{r}"}
        for r, s in enumerate(model["sources"])]
    slots = {name: {"workers": ws, "endpoint": f"http://{name}", "parked": True,
                    "command": str(directory / (name+".command.json"))}
             for name, ws in (("target_a", [0, 1]), ("target_b", [2, 3]))}
    profile = {"rates": {"local_dram": 35, "local_hbm": 80, "peer": 35, "network": 20},
               "gpu_ingress_GBps": 80, "gpu_egress_GBps": 80, "dram_GBps": 50,
               "network_node_GBps": 30, "serial_target_execution": True}
    root = directory / "root.json"
    support.write(root, {"endpoints": [f"http://w{w}" for w in range(4)], "members": [[w["node"], w["gpu"]] for w in workers],
                         "nodes": {"node27": {}, "node29": {}}, "handoff": str(directory / "handoff")})
    state = {"root_session": str(root), "models": {"B": model}, "profile": profile, "slots": slots}
    session = directory / "session.json"
    support.write(session, state)
    return session, model, workers, base, slots


class LayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=CHECK_ROOT)
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.session, self.model, self.workers, self.base, self.slots = fixture(self.directory)
        self.protocol = json.loads(PROTOCOL.read_text())

    def args(self, scene="S4"):
        return argparse.Namespace(session=self.session, out=self.directory/"run", scene=scene,
            target_slot="target_a" if scene == "S4" else "target_b", cache_slot="target_a", source_slot="target_a",
            methods=self.protocol[scene]["methods"], repetitions=1, guard_mib=2048,
            allocation_tolerance_mib=32, physical_tolerance_mib=256)

    def runner(self, scene="S4"):
        with patch("run_scenarios.Cluster") as constructor:
            runner = LayoutScenarioRun(self.args(scene))
        constructor.return_value.profile = json.loads(self.session.read_text())["profile"]
        return runner, constructor.return_value

    def case(self, scene="S4"):
        runner, _ = self.runner(scene)
        layout = cache_layout(self.model, self.slots["target_a"], self.protocol[scene])[0]
        replicas = copy.deepcopy(self.base)
        for i, row in enumerate(layout):
            rid = f"a_cache{i}"
            runner.owned.append(rid)
            replicas.append(dict(row, id=rid, generation=1, state="VALID", numa=0, epoch=f"epoch{row['worker']}"))
        targets = [{"key": f"live{r}", "identity": s["identity"], "tp": r, "worker": runner.target_slot["workers"][r],
                    "tier": "hbm", "off": 0, "n": s["n"], "mode": "live"} for r, s in enumerate(self.model["sources"])]
        return runner, runner.case(self.workers, replicas, targets)

    def plan(self, scene="S5", strategy="fastest"):
        runner, case = self.case(scene)
        a = assess_case(case, strategy)
        self.assertEqual(a["status"], "applicable", a)
        ids = set(a["source_ids"])
        plan = restore_plan([r for r in case["replicas"] if r["id"] in ids], case["targets"], case["workers"], case["profile"], a["strategy"])
        self.assertEqual(plan["strategy"], a["strategy"])
        return runner, case, plan

    def test_registration_cpu_only_and_preserves_S5_boundary(self):
        with patch("run_scenarios.http") as http:
            runner, cluster = self.runner("S5")
        http.assert_not_called()
        cluster.inventory.assert_not_called()
        cluster.transfer.assert_not_called()
        self.assertEqual(runner.result["scenario_id"], "S5")
        self.assertEqual(runner.result["variant"], "software_worker_serial")
        self.assertEqual(runner.result["physical_resource_gate"], "not_measured")
        self.assertIsNone(runner.source_slot)

    def test_both_layouts_use_exact_budget_and_location_cap(self):
        for scene in ("S4", "S5"):
            _, needed = cache_layout(self.model, self.slots["target_a"], self.protocol[scene])
            self.assertEqual(sum(needed.values()), 8*GIB)
            self.assertEqual(needed, {0: 4*GIB, 1: 4*GIB})

    def test_layout_rejects_extent_and_nonfragmented_budget(self):
        config = copy.deepcopy(self.protocol["S4"])
        config["assignments"][0][3] = 33*GIB
        with self.assertRaises(ValueError):
            cache_layout(self.model, self.slots["target_a"], config)
        config = copy.deepcopy(self.protocol["S4"])
        config["assignments"][1][1] = 0
        with self.assertRaises(ValueError):
            cache_layout(self.model, self.slots["target_a"], config)

    def test_S4_strong_fastest_uses_all_8GiB_HBM(self):
        _, _, plan = self.plan("S4")
        self.assertEqual(sum(o["n"] for o in plan["ops"] if o["replica_id"].startswith("a_cache")), 8*GIB)

    def test_S4_janus_host_is_feasible_complete_local_base(self):
        _, _, plan = self.plan("S4", "janus_host")
        self.assertEqual({o["replica_id"] for o in plan["ops"]}, {"base0", "base1"})
        self.assertEqual(sum(o["n"] for o in plan["ops"]), sum(s["n"] for s in self.model["sources"]))

    def test_S4_existing_LP_has_no_fallback(self):
        _, _, plan = self.plan("S4", "resource_lp")
        self.assertIsNone(plan["fallback_reason"])
        validate_plan(plan)

    def test_S5_physical_gate_still_pending(self):
        _, case = self.case("S5")
        case["scenario_id"] = "S5"
        self.assertEqual(assess_case(case, "fastest")["reasons"], ["shared_resource_measurement_missing"])

    def test_S5_fixed_route_multiset_and_per_target_order(self):
        _, _, plan = self.plan()
        ordered = reordered_plan(plan, "rank_interleaved")
        self.assertEqual(control_signature(plan), control_signature(ordered))
        self.assertNotEqual(dependency_report(plan)["global_order"], dependency_report(ordered)["global_order"])
        for target in plan["targets"]:
            self.assertEqual([o for o in plan["ops"] if o["target"] == target["key"]],
                             [o for o in ordered["ops"] if o["target"] == target["key"]])
        validate_plan(ordered)
        self.assertEqual(sum(o["n"] for o in plan["ops"] if o["replica_id"].startswith("a_cache")), 8*GIB)

    def test_S5_moves_shared_worker_rank1_prefix_before_rank0_tail(self):
        _, _, plan = self.plan()
        reordered = reordered_plan(plan, "rank_interleaved")
        def worker0(plan):
            return [(o["tp"], o["replica_id"], o["off"]) for o in plan["ops"] if o["src_w"] == 0]
        self.assertEqual(worker0(plan), [(0, "a_cache0", 0), (0, "base0", 4*GIB), (1, "a_cache2", 0)])
        self.assertEqual(worker0(reordered), [(0, "a_cache0", 0), (1, "a_cache2", 0), (0, "base0", 4*GIB)])

    def test_control_signature_normalizes_only_target_phase_key(self):
        _, _, plan = self.plan()
        second = copy.deepcopy(plan)
        for op in second["ops"]:
            op["target"] = "next_phase/"+op["target"]
        self.assertEqual(control_signature(plan), control_signature(second))
        second["ops"][0]["generation"] += 1
        self.assertNotEqual(control_signature(plan), control_signature(second))

    def test_S5_rejects_changed_source_and_missing_shared_worker(self):
        runner, _, plan = self.plan()
        runner.scheduled(plan)
        changed = copy.deepcopy(plan)
        changed["ops"][0]["replica_id"] = "other"
        with self.assertRaises(AssertionError):
            runner.scheduled(changed)
        runner.canonical_ops = None
        for o in changed["ops"]:
            o["src_w"] = o["tp"]
        with self.assertRaises(ValueError):
            runner.scheduled(changed)

    def test_unknown_schedule_and_invalid_coverage_rejected(self):
        _, _, plan = self.plan()
        with self.assertRaises(ValueError):
            reordered_plan(plan, "source_random")
        plan["ops"][0]["n"] -= 1
        with self.assertRaises(ValueError):
            reordered_plan(plan, "rank_interleaved")

    def test_pending_consumer_preserves_cache_owners(self):
        runner, cluster = self.runner("S5")
        cluster.active = {}
        runner.owned = ["cache"]
        runner.unreleased_slots = {"http://target": {"endpoint": "http://target"}}
        runner.resume_futures = {"http://target": Future()}
        with patch("run_scenarios.http") as http, patch.object(runner, "memory", side_effect=RuntimeError("inventory unavailable")):
            runner.cleanup()
        http.assert_not_called()
        cluster.retire.assert_not_called()
        self.assertEqual(runner.owned, ["cache"])

    def test_unmap_failure_preserves_consumer_and_cache(self):
        runner, cluster = self.runner("S4")
        cluster.active = {"tx": {}}
        cluster.finish.side_effect = RuntimeError("unmap failed")
        runner.owned = ["cache"]
        runner.unreleased_slots = {"http://target": {"endpoint": "http://target"}}
        with patch("run_scenarios.http") as http, patch.object(runner, "memory", side_effect=RuntimeError("inventory unavailable")):
            runner.cleanup()
        http.assert_not_called()
        cluster.retire.assert_not_called()

    def test_cache_preflight_requires_target_plus_cache_headroom(self):
        runner, cluster = self.runner("S4")
        runner.before = copy.deepcopy(self.workers)
        runner.before[0]["headroom_bytes"]["hbm"] = self.model["sources"][0]["n"] + 2*GIB
        with patch("run_scenarios.ScenarioRun.preflight"):
            with self.assertRaises(MemoryError):
                runner.preflight()
        cluster.transfer.assert_not_called()

    def test_runtime_evidence_checks_actual_order_and_source_semantics(self):
        _, _, plan = self.plan()
        plan = reordered_plan(plan, "rank_interleaved")
        execution = [{"worker": wid, "records": [{"op_id": op["op_id"], "replica_id": op["replica_id"], "bytes": op["n"],
            "kind": op["kind"], "role": "source" if wid == op["src_w"] else "target"}
            for op in plan["ops"] if wid in (op["src_w"], op["dst_w"])]} for wid in range(4)]
        self.assertTrue(verify_runtime_order(plan, execution)["passed"])
        wrong = copy.deepcopy(execution)
        wrong[0]["records"].reverse()
        with self.assertRaises(AssertionError):
            verify_runtime_order(plan, wrong)
        wrong = copy.deepcopy(execution)
        wrong[0]["records"][0]["bytes"] -= 1
        with self.assertRaises(AssertionError):
            verify_runtime_order(plan, wrong)
        with self.assertRaises(AssertionError):
            verify_runtime_order(plan, execution[:3])

    def test_S5_rejects_unparked_cache_engine_before_transfer(self):
        runner, cluster = self.runner("S5")
        runner.before = self.workers
        runner.result["parked_preflight"] = {}
        runner.cache_slot["parked"] = False
        with patch("run_scenarios.ScenarioRun.preflight"), patch("run_scenarios.http") as http:
            with self.assertRaises(ValueError):
                runner.preflight()
        http.assert_not_called()
        cluster.transfer.assert_not_called()

    def test_S4_same_slot_does_not_repeat_parked_request(self):
        runner, _ = self.runner("S4")
        runner.before = self.workers
        with patch("run_scenarios.ScenarioRun.preflight"), patch.object(runner, "parked_evidence") as parked:
            runner.preflight()
        parked.assert_not_called()

    def test_selected_bytes_distinguish_shared_budget_from_Janus_usage(self):
        _, case, plan = self.plan("S4", "janus_host")
        ledger = assess_case(case, "janus_host")["resource_ledger"]
        self.assertEqual(ledger["extra_cache_hbm_bytes"], 8*GIB)
        self.assertEqual(selected_source_bytes(plan, ledger), {"hbm": 0, "dram": sum(s["n"] for s in self.model["sources"])})

    def test_reverse_schedules_preserves_protocol_and_operation_multiset(self):
        runner, _, plan = self.plan()
        before = copy.deepcopy(self.protocol)
        config = schedule_config(self.protocol, "S5", True)
        self.assertEqual(self.protocol, before)
        self.assertEqual(config["schedules"], ["rank_interleaved", "target_major"])
        plans = [reordered_plan(plan, s) for s in config["schedules"]]
        self.assertEqual(control_signature(plans[0]), control_signature(plans[1]))
        for t in plan["targets"]:
            self.assertEqual([o for o in plans[0]["ops"] if o["target"] == t["key"]],
                             [o for o in plans[1]["ops"] if o["target"] == t["key"]])
        runner.scheduled(plans[0])
        runner.scheduled(plans[1])

    def test_reverse_schedules_runs_and_records_actual_reverse_order(self):
        args = self.args("S5")
        args.reverse_schedules = True
        with patch("run_scenarios.Cluster"):
            runner = LayoutScenarioRun(args)
        seen = []
        with patch.object(runner, "preflight"), patch.object(runner, "build_cache"), patch.object(runner, "cleanup"), \
                patch.object(runner, "restore", side_effect=lambda method, repetition: seen.append((runner.schedule, method, repetition))):
            self.assertTrue(runner.run())
        expected = ["rank_interleaved", "target_major"]
        self.assertEqual([row[0] for row in seen], expected)
        self.assertEqual(runner.result["schedule_execution_order"], expected)
        self.assertEqual(runner.result["protocol"]["schedule_execution_order"], expected)
        self.assertEqual(runner.protocol["S5"]["schedules"], ["target_major", "rank_interleaved"])

    def test_reverse_schedules_S4_constructor_rejected_before_registration(self):
        args = self.args("S4")
        args.reverse_schedules = True
        with patch("run_scenarios.Cluster") as cluster:
            with self.assertRaises(ValueError):
                LayoutScenarioRun(args)
        cluster.assert_not_called()
        self.assertFalse(args.out.exists())

    def test_reverse_schedules_S4_CLI_rejected_before_runner(self):
        argv = ["run_layout_scenarios.py", "--session", str(self.session), "--out", str(self.directory/"run"),
                "--scene", "S4", "--reverse-schedules"]
        with patch("sys.argv", argv), patch.object(layout_module, "LayoutScenarioRun") as constructor, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as exc:
                layout_module.main()
        self.assertEqual(exc.exception.code, 2)
        constructor.assert_not_called()


def main():
    global CHECK_ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    CHECK_ROOT = support.new_run(args.out)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(LayoutTests))
    (CHECK_ROOT / "unittest.log").write_text(stream.getvalue())
    summary = {"status": "passed" if result.wasSuccessful() else "failed", "tests_run": result.testsRun,
               "errors": len(result.errors), "failures": len(result.failures), "measurement_status": "not_measured",
               "scope": "CPU fixtures, real planners and lifecycle mocks; no GPU observations", "code_sha256": support.code_freeze(),
               "protocol_sha256": support.digest(PROTOCOL)}
    support.write(CHECK_ROOT / "summary.json", summary)
    print(json.dumps({k: v for k, v in summary.items() if k != "code_sha256"}))
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    main()
