"""Meaningful CPU gates for the BlitzScale source port and actual upstream oracles."""
import argparse
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT / "main_lib"))
from fluidckpt.benchmarks.blitz_adapter import (BlitzScaleAdapter, SteersmanPort, UPSTREAM_SHA,
                                              rdma_tp_chains, span_max_rdma_sending_replicas)
from fluidckpt.cluster.planning import validate_plan


class BlitzTests(unittest.TestCase):
    oracle = None

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.evidence = Path(self.temp.name) / "source.json"
        self.evidence.write_text('{"source": "CPU fixture only"}')

    def case(self):
        workers = [dict(worker=i, node="node27" if i < 2 else "node29", gpu=i % 2, numa=0,
                        capabilities=["local_dram", "local_hbm", "network", "peer"]) for i in range(4)]
        replicas = [dict(id=f"dram/{rank}", identity=f"different-shard-{rank}", tp=rank,
                         off=0, n=4096, tier="dram", worker=rank, numa=0, generation=1,
                         epoch="keeper-v1", state="VALID", source_role="base") for rank in range(2)]
        replicas += [dict(r, id="active/"+str(r["tp"]), tier="hbm", source_role="active") for r in replicas]
        targets = [dict(key=f"destination/{r}", identity=f"different-shard-{r}", tp=r,
                        off=0, n=4096, worker=r+2, tier="hbm") for r in range(2)]
        return dict(scenario_id="S3", workers=workers, replicas=replicas, targets=targets,
            active_instances=[dict(logical_id="actual/TP2", replica_ids=["active/0", "active/1"],
                survival_evidence=dict(path=str(self.evidence), alive_before=True, protected_during_copy=True,
                                       external_existing=False))],
            profile=dict(rates=dict(local_dram=100, local_hbm=1, peer=2, network=3),
                gpu_ingress_GBps=10, gpu_egress_GBps=10, dram_GBps=10, network_node_GBps=10),
            format_contract=dict(tp=2, model_version="fixture", dtype="bf16", quantization="none",
                                 processed_snapshot_id=["different-shard-0", "different-shard-1"]),
            budget=dict(base_dram_bytes=8192, extra_hbm_bytes=0, location_extra_hbm_bytes={},
                        target_allocation_bytes=8192, staging_bytes=0))

    def test_actual_upstream_cache_oracle(self):
        self.assertIsNotNone(self.oracle, "compile and supply --oracle for the fidelity gate")
        self.assertEqual(self.oracle["commit"], UPSTREAM_SHA)
        now = [0.0]
        port = SteersmanPort(100000, lambda: now[0])
        for rank in (0, 2, 4):
            port.register_replica(rank, 27)
        port.record_model_loaded(0, "qwen")
        actual = {"local": port.assign_nvlink_chains("qwen", [2, 4])}
        port.wait_replica_nvlink(0, 1)
        actual["busy"] = port.assign_nvlink_chains("qwen", [2])
        port.post_replica_nvlink(0)
        port.record_model_unloaded(0, "qwen")
        actual["ttl_empty"] = port.machine_has_cache(2, "qwen")
        actual["ttl_no_sender"] = port.assign_nvlink_chains("qwen", [2])
        port.ttl_ms = 0
        actual["expired"] = port.machine_has_cache(2, "qwen")
        port.record_model_loaded(0, "qwen")
        actual["loaded_beyond_ttl"] = port.machine_has_cache(2, "qwen")
        expected_lines = ["local=[(Some(0), 2), (Some(0), 4)]", "busy=[(None, 2)]", "ttl_empty=true",
                          "ttl_no_sender=[(None, 2)]", "expired=false", "loaded_beyond_ttl=true"]
        self.assertEqual(self.oracle["rust_stdout"].splitlines()[:6], expected_lines)
        self.assertEqual(actual, {"local": [(0, 2), (0, 4)], "busy": [(None, 2)], "ttl_empty": True,
                                  "ttl_no_sender": [(None, 2)], "expired": False, "loaded_beyond_ttl": True})

    def test_actual_upstream_source_order_and_TP_stride_oracle(self):
        senders = span_max_rdma_sending_replicas([2, 4], [6], [8], {4: "RdmaSending"})
        self.assertEqual(senders, [2, 6, 8])
        self.assertIn("sender_order=[2, 6, 8]\nselected_source=8", self.oracle["rust_stdout"])
        self.assertEqual(senders.pop(), 8)
        lines = []
        for count, destinations in [(1, [[2, 3]]), (2, [[4, 5], [2, 3]])]:
            for tp, chain in enumerate(rdma_tp_chains([0, 1], destinations)):
                lines.append(f"tp{count}:{tp}="+"".join(f"{rank}," for rank in chain))
        self.assertEqual(lines, self.oracle["cpp_stdout"].splitlines())

    def test_real_plan_uses_distinct_rank_contents_not_fastest_host(self):
        case = self.case()
        plan = BlitzScaleAdapter().plan(case)
        validate_plan(plan)
        self.assertEqual(plan["blitz"]["tp_chains"], [[0, 2], [1, 3]])
        self.assertEqual([(o["src_w"], o["dst_w"], o["identity"]) for o in plan["ops"]],
                         [(0, 2, "different-shard-0"), (1, 3, "different-shard-1")])
        self.assertEqual(plan["resource_ledger"]["incremental_active_hbm_bytes"], 8192)
        self.assertEqual(plan["blitz"]["origin"]["commit"], UPSTREAM_SHA)
        self.assertIsNone(plan["fallback_reason"])

    def test_local_full_group_cache_has_priority_over_remote_group_order(self):
        case = self.case()
        for worker in case["workers"][:2]:
            worker["node"] = "node29"
        case["workers"] += [dict(case["workers"][i], worker=i+4, node="node27") for i in range(2)]
        remote = [dict(r, id="remote/"+str(r["tp"]), worker=r["tp"]+4)
                  for r in case["replicas"] if r["tier"] == "hbm"]
        case["replicas"] += remote
        case["active_instances"].append(dict(case["active_instances"][0], logical_id="remote/TP2",
                                             replica_ids=[r["id"] for r in remote]))
        plan = BlitzScaleAdapter().plan(case)
        self.assertEqual(plan["blitz"]["source_group"], "actual/TP2")
        self.assertEqual(plan["blitz"]["source_mode"], "local_cached_TP_group")

    def test_remote_source_pop_order_does_not_rank_candidates_by_rate(self):
        case = self.case()
        case["workers"] += [dict(case["workers"][i], worker=i+4, node="node28") for i in range(2)]
        remote = [dict(r, id="last/"+str(r["tp"]), worker=r["tp"]+4)
                  for r in case["replicas"] if r["tier"] == "hbm"]
        case["replicas"] += remote
        case["active_instances"].append(dict(case["active_instances"][0], logical_id="last/TP2",
                                             replica_ids=[r["id"] for r in remote]))
        case["profile"]["directed_path_rates"] = {"0:hbm:0->2": 100, "1:hbm:0->3": 100,
                                                   "4:hbm:0->2": 1, "5:hbm:0->3": 1}
        plan = BlitzScaleAdapter().plan(case)
        self.assertEqual(plan["blitz"]["source_group"], "last/TP2")
        self.assertEqual(plan["blitz"]["tp_chains"], [[4, 2], [5, 3]])

    def test_incomplete_group_is_not_reconstructed_across_instances(self):
        case = self.case()
        case["active_instances"][0]["replica_ids"] = ["active/0"]
        case["active_instances"].append(dict(case["active_instances"][0], logical_id="other", replica_ids=["active/1"]))
        with self.assertRaisesRegex(ValueError, "not_supported"):
            BlitzScaleAdapter().plan(case)

    def test_rank_identity_cannot_be_swapped(self):
        case = self.case()
        case["replicas"][-1]["identity"] = "different-shard-0"
        with self.assertRaisesRegex(ValueError, "not_supported"):
            BlitzScaleAdapter().plan(case)

    def test_missing_live_protection_and_stale_path_are_rejected(self):
        for failure in ("protected", "missing", "path"):
            case = self.case()
            if failure == "protected":
                case["active_instances"][0]["survival_evidence"]["protected_during_copy"] = False
            elif failure == "missing":
                case["active_instances"][0]["survival_evidence"]["path"] = "/absent/fixture"
            else:
                case["profile"]["verified_paths"] = []
            with self.assertRaisesRegex(ValueError, "not_supported"):
                BlitzScaleAdapter().plan(case)

    def test_generation_epoch_and_tail_offsets_are_preserved(self):
        case = self.case()
        first = case["replicas"][-2]
        first["n"] = 2048
        case["replicas"].append(dict(first, id="active/tail", off=2048, n=2048, generation=7, epoch="tail-epoch"))
        case["active_instances"][0]["replica_ids"].append("active/tail")
        plan = BlitzScaleAdapter().plan(case)
        tail = next(o for o in plan["ops"] if o["replica_id"] == "active/tail")
        self.assertEqual((tail["source_offset"], tail["off"], tail["generation"], tail["source_epoch"]),
                         (0, 2048, 7, "tail-epoch"))

    def test_transfer_failure_releases_planner_protection(self):
        adapter = BlitzScaleAdapter()
        plan = adapter.plan(self.case())
        class FailingBackend:
            def transfer(self, plan, **kwargs):
                if adapter.steersman.rank_nvlink_sem[0] != 1:
                    raise AssertionError("source not protected during transfer")
                raise RuntimeError("injected backend abort")
        with self.assertRaisesRegex(RuntimeError, "injected backend abort"):
            adapter.transfer(FailingBackend(), plan)
        self.assertEqual(adapter.steersman.rank_nvlink_sem[0], 0)

    def test_source_unload_keeps_TTL_but_cannot_create_a_sender(self):
        adapter = BlitzScaleAdapter()
        case = self.case()
        adapter.plan(case)
        model_key = adapter.loaded[0][0]
        case["active_instances"] = []
        with self.assertRaisesRegex(ValueError, "not_supported"):
            adapter.plan(case)
        self.assertTrue(adapter.steersman.machine_has_cache(0, model_key))
        self.assertIsNone(adapter.steersman.find_one_rank_within_cache("node27", model_key))

    def test_multiple_target_mode_fails_explicitly(self):
        case = self.case()
        case["targets"] += copy.deepcopy(case["targets"])
        with self.assertRaisesRegex(ValueError, "multi-target"):
            BlitzScaleAdapter().plan(case)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    BlitzTests.oracle = json.loads(args.oracle.read_text())
    log = io.StringIO()
    result = unittest.TextTestRunner(stream=log, verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(BlitzTests))
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "tests.log").write_text(log.getvalue())
    report = {"passed": result.wasSuccessful(), "tests": result.testsRun, "failures": len(result.failures),
              "errors": len(result.errors), "commit": UPSTREAM_SHA, "oracle": str(args.oracle.resolve()),
              "scope": "CPU algorithm/source contracts only; no GPU performance measurement"}
    (args.out / "tests.json").write_text(json.dumps(report, indent=2)+"\n")
    print(log.getvalue())
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    main()
