"""CPU acceptance for exact routing, role evidence, budgeting and owned retirement."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import runner
from fluidckpt.cluster.planning import restore_plan


class LayoutTests(unittest.TestCase):
    def plan(self, name, locations):
        case, _ = runner.original.block_case(runner.BLOCK_BYTES, runner.DEFAULT_CHUNK)
        selected = [case["candidates"][2*tp+w] for tp,w in enumerate(locations)]
        plan = runner.interleave(restore_plan(selected,case["restore_scenarios"][0]["targets"],
                                               case["workers"],runner.PROFILE,"nearest"))
        plan.update(executor="serial",chunk_bytes=runner.DEFAULT_CHUNK)
        return plan

    def test_four_paths_same_bytes_and_order(self):
        expected = {"A": [(0,0),(1,1)], "B": [(0,0),(0,1)],
                    "C": [(1,0),(1,1)], "D": [(1,0),(0,1)]}
        for name,locations in runner.LAYOUTS:
            plan = self.plan(name,locations)
            runner.assert_plan(plan,name,locations)
            self.assertEqual([(o["src_w"],o["dst_w"]) for o in plan["ops"]],expected[name])
            self.assertEqual(sum(o["n"] for o in plan["ops"]),32<<20)
            self.assertEqual(runner.contract()["cache_hbm_bytes"],32<<20)
        self.assertEqual([runner.contract()["layouts"][n]["network_payload_bytes"] for n in "ABCD"],
                         [0,16<<20,16<<20,32<<20])

    def test_changed_route_bytes_kind_or_order_rejected(self):
        plan = self.plan("D",(1,0))
        for key,value in (("src_w",0),("n",1),("kind","local_hbm"),("op_id",5)):
            bad = copy.deepcopy(plan)
            bad["ops"][0][key] = value
            with self.assertRaises(ValueError): runner.assert_plan(bad,"D",(1,0))
        bad = copy.deepcopy(plan)
        bad["ops"].reverse()
        with self.assertRaises(ValueError): runner.assert_plan(bad,"D",(1,0))

    def measured(self,name):
        ops = runner.contract()["layouts"][name]["ops"]
        return dict(transfer=dict(execution=[dict(worker=w,records=[dict(op_id=o["op_id"],
            bytes=o["n"],kind=o["kind"],role=o["worker_roles"][str(w)])
            for o in ops if str(w) in o["worker_roles"]]) for w in (0,1)]))

    def test_worker_roles_bytes_order_checked(self):
        for name,_ in runner.LAYOUTS:
            runner.assert_execution(self.measured(name),name)
        for field,value in (("bytes",1),("role","target"),("kind","network")):
            measured = self.measured("A")
            measured["transfer"]["execution"][0]["records"][0][field] = value
            with self.assertRaises(ValueError): runner.assert_execution(measured,"A")
        measured = self.measured("D")
        measured["transfer"]["execution"][0]["records"].reverse()
        with self.assertRaises(ValueError): runner.assert_execution(measured,"D")

    def budget(self,runtime=0):
        return dict(passed=True,resources=[dict(worker=w,fixed_runtime_dram_increment_bytes=runtime,
            fixed_runtime_hbm_increment_bytes=runtime,additional_application_dram_bound_bytes=(64 if w==0 else 32)<<20,
            additional_application_hbm_bound_bytes=(48 if w==0 else 32)<<20) for w in (0,1)])

    def test_common_hbm_bounds_and_dynamic_margin(self):
        with patch.object(runner.original,"mesh_resource_budget",return_value=self.budget()):
            result = runner.budget_gate({},[])
        self.assertTrue(result["passed"])
        self.assertEqual([r["additional_application_hbm_bound_bytes"] for r in result["resources"]],[48<<20]*2)
        self.assertEqual([r["projected_dram_increment_bytes"] for r in result["resources"]],[72<<20,40<<20])
        with patch.object(runner.original,"mesh_resource_budget",return_value=self.budget(runner.original.TASK_BUDGET-(64<<20))):
            self.assertFalse(runner.budget_gate({},[])["passed"])

    def test_budget_failure_preserves_meta_before_any_allocation(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            specfile = out/"spec.json"
            specfile.write_text("{}")
            spec = dict(_source_path=str(specfile),actual_launch_plan_ref=runner.evidence_ref(specfile),
                        workers=[dict(endpoint="CPU0"),dict(endpoint="CPU1")])
            guarded = dict(session="CPU",fixed_preparation={},mesh_inventory=[])
            with patch.object(runner.original,"launch_plan"), patch.object(runner.original,"execute",return_value=guarded), patch.object(runner,"Cluster"), \
                 patch.object(runner,"checked_inventory",return_value=([],[])), \
                 patch.object(runner,"budget_gate",return_value=dict(passed=False)), patch.object(runner,"request") as rpc:
                result = runner.execute(spec,out)
            rpc.assert_not_called()
            self.assertEqual(result["source_allocations"],0)
            self.assertEqual(json.loads((out/"execution.json").read_text())["status"],"budget_rejected_before_synth")

    def test_preflight_failure_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            specfile = out/"spec.json"
            specfile.write_text("{}")
            with patch.object(runner.original,"launch_plan"), patch.object(runner.original,"execute",side_effect=ValueError("expired guard")), patch.object(runner,"request") as rpc:
                with self.assertRaisesRegex(ValueError,"expired guard"):
                    runner.execute(dict(_source_path=str(specfile),actual_launch_plan_ref=runner.evidence_ref(specfile)),out)
            rpc.assert_not_called()
            record = json.loads((out/"execution.json").read_text())
            self.assertEqual(record["status"],"failed")
            self.assertEqual(record["source_allocations"],0)
            self.assertTrue(record["source_refs"])

    def test_cleanup_failure_does_not_retire_unowned_or_skip_remaining(self):
        class Controller:
            endpoints = ["CPU"]
            def inventory(self):
                return [dict(replicas=[dict(id="a",state="VALID"),dict(id="b",state="VALID"),
                                      dict(id="foreign",state="VALID"),dict(id="released",state="RELEASED")])],[]
        called = []
        def retire(ep,operation,body):
            called.append(body["id"])
            if body["id"]=="b": raise RuntimeError("injected")
        with patch.object(runner.original,"request",side_effect=retire):
            errors = runner.original.cleanup_owned(Controller(),[(0,"a"),(0,"b"),(0,"released")])
        self.assertEqual(called,["b","a"])
        self.assertEqual(len(errors),1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
