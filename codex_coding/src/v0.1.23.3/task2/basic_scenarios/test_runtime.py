"""CPU validation of measurement guards and conservative cleanup ownership."""
import argparse
from concurrent.futures import Future
import contextlib
import io
import json
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import Mock, patch
import runtime_support as support
from inspect_runtime import inspect
from run_scenarios import ScenarioRun

CHECK_ROOT = None


def fixture(directory):
    model = {"tp": 2, "sources": [{"identity": f"rank{r}", "tp": r, "n": 4096,
             "meta": {"model_identity": "fixture", "tensors": []}, "index": {"weight": [0, 4096]}}
             for r in range(2)], "source_ids": ["base0", "base1"], "reference": {"text": "ok"}}
    root = directory / "root.json"
    support.write(root, {"endpoints": ["http://w0", "http://w1"], "members": [["node27", 0], ["node27", 1]],
                        "nodes": {"node27": {"container": "fixture"}}, "handoff": str(directory / "handoff")})
    state = {"root_session": str(root), "models": {"B": model}, "profile": {},
             "slots": {"target_b": {"workers": [0, 1], "endpoint": "http://engine", "parked": True,
                                     "command": str(directory / "command.json")}}}
    path = directory / "session.json"
    support.write(path, state)
    return path, model


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=CHECK_ROOT)
        self.directory = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def args(self):
        path, _ = fixture(self.directory)
        return argparse.Namespace(session=path, out=self.directory / "run", scene="S2", target_slot="target_b",
            source_slot="target_a", methods=["janus_host", "resource_lp"], repetitions=1,
            guard_mib=2, allocation_tolerance_mib=32, physical_tolerance_mib=256)

    def test_run_directory_never_reused(self):
        path = self.directory / "unique"
        support.new_run(path)
        with self.assertRaises(FileExistsError):
            support.new_run(path)

    def test_health_refused_before_network(self):
        with patch("urllib.request.build_opener") as opener:
            with self.assertRaises(ValueError):
                support.http("http://engine", "health")
            opener.assert_not_called()

    def test_retained_requires_complete_exact_dram(self):
        _, model = fixture(self.directory)
        rows = [{"id": f"base{r}", "identity": f"rank{r}", "tp": r, "off": 0,
                 "n": 4096, "state": "VALID", "tier": "dram"} for r in range(2)]
        self.assertEqual(len(support.select_base(model, rows)), 2)
        rows[1]["n"] = 2048
        with self.assertRaises(ValueError):
            support.select_base(model, rows)
        rows[1].update(n=4096, tier="hbm")
        with self.assertRaises(ValueError):
            support.select_base(model, rows)

    def test_registration_makes_no_network_or_gpu_request(self):
        with patch("run_scenarios.http") as http, patch("run_scenarios.Cluster") as cluster:
            runner = ScenarioRun(self.args())
            self.assertEqual(runner.result["status"], "registered")
            self.assertEqual(runner.result["measurement_status"], "not_measured")
            http.assert_not_called()
            cluster.return_value.inventory.assert_not_called()
            cluster.return_value.transfer.assert_not_called()

    def test_cleanup_retains_owner_if_consumer_release_uncertain(self):
        with patch("run_scenarios.Cluster") as constructor:
            runner = ScenarioRun(self.args())
        cluster = constructor.return_value
        cluster.active = {}
        runner.owned = ["new/owner"]
        runner.unreleased_slots = {"http://source": {"endpoint": "http://source"}}
        with patch("run_scenarios.http", side_effect=RuntimeError("release failed")), patch.object(runner, "memory", side_effect=RuntimeError("inventory failed")):
            runner.cleanup()
        cluster.retire.assert_not_called()
        self.assertEqual(runner.result["remaining_owned_ids"], ["new/owner"])
        self.assertTrue(any("retain owner" in e for e in runner.result["cleanup_errors"]))

    def test_cleanup_retains_allocations_if_verification_unmap_fails(self):
        with patch("run_scenarios.Cluster") as constructor:
            runner = ScenarioRun(self.args())
        cluster = constructor.return_value
        cluster.active = {"tx": {}}
        cluster.finish.side_effect = RuntimeError("unmap failed")
        runner.owned = ["new/owner"]
        runner.unreleased_slots = {"http://source": {"endpoint": "http://source"}}
        with patch("run_scenarios.http") as http, patch.object(runner, "memory", side_effect=RuntimeError("inventory failed")):
            runner.cleanup()
        http.assert_not_called()
        cluster.retire.assert_not_called()

    def test_inspect_records_all_endpoint_errors(self):
        path, _ = fixture(self.directory)
        args = argparse.Namespace(session=path, out=self.directory / "inspect", nodes=[], timeout=1)
        with patch("inspect_runtime.http", side_effect=ConnectionRefusedError(111, "refused")), contextlib.redirect_stdout(io.StringIO()):
            result = inspect(args)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["service_classification"]["failed_keeper_count"], 2)
        self.assertEqual(len(result["endpoint_probes"]), 2)
        self.assertTrue(all("ConnectionRefusedError" in r["error"] for r in result["endpoint_probes"]))
        self.assertTrue((args.out / "summary.json").exists())

    def test_pending_resume_cleanup_is_bounded_and_preserves_owner(self):
        with patch("run_scenarios.Cluster") as constructor:
            runner = ScenarioRun(self.args())
        cluster = constructor.return_value
        cluster.active = {}
        runner.owned = ["new/owner"]
        runner.unreleased_slots = {"http://source": {"endpoint": "http://source"}}
        runner.resume_futures = {"http://source": Future()}
        with patch("run_scenarios.http") as http, patch.object(runner, "memory", side_effect=RuntimeError("inventory failed")):
            runner.cleanup()
        http.assert_not_called()
        cluster.retire.assert_not_called()
        self.assertEqual(runner.result["pending_resume_endpoints"], ["http://source"])

    def test_infeasible_method_is_rejected_before_resume_or_command_write(self):
        with patch("run_scenarios.Cluster") as constructor:
            runner = ScenarioRun(self.args())
        constructor.return_value.inventory.return_value = ([], [])
        with patch.object(runner, "case", return_value={}), patch("run_scenarios.assess_case", return_value={"status": "pending", "driver": None}), patch.object(runner, "start_resume") as resume, patch.object(runner, "command") as command:
            with self.assertRaises(ValueError):
                runner.restore("resource_lp", 0)
        resume.assert_not_called()
        command.assert_not_called()

    def test_pre_resume_command_is_restored_on_export_failure(self):
        args = self.args()
        old = b'{"metrics":"old"}'
        path = self.directory / "command.json"
        path.write_bytes(old)
        with patch("run_scenarios.Cluster") as constructor:
            runner = ScenarioRun(args)
        constructor.return_value.active = {}
        runner.command(runner.target_slot, {"metrics": "new"})
        with patch.object(runner, "memory", side_effect=RuntimeError("inventory failed")):
            runner.cleanup()
        self.assertEqual(path.read_bytes(), old)

    def test_bootstrap_registration_never_starts_a_container(self):
        path, _ = fixture(self.directory)
        support.write(self.directory / "model_B_node27_reclaim02.process.json", {"command": ["python3", "launch.py"], "env": {}})
        root = json.loads((self.directory / "root.json").read_text())
        root["nodes"]["node29"] = {"container": "fixture_remote", "ip": "12.11.3.29"}
        root["nodes"]["node27"]["ip"] = "12.11.3.27"
        support.write(self.directory / "root.json", root)
        args = argparse.Namespace(reference_session=path, out=self.directory / "bootstrap", execute=False,
                                  port_base=33000, source_memory_gib=160, target_memory_gib=64,
                                  model_path=self.directory)
        sys.path.insert(0, str(support.ROOT / "codex_coding/src/task2/cluster"))
        with patch("runtime_support.host_command") as command, patch("run_system.SystemRun.docker_host") as docker:
            result = support.bootstrap(args)
        self.assertEqual(result["status"], "registered_only")
        command.assert_not_called()
        docker.assert_not_called()


def main():
    global CHECK_ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    CHECK_ROOT = support.new_run(args.out)
    stream = io.StringIO()
    tests = unittest.defaultTestLoader.loadTestsFromTestCase(RuntimeTests)
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(tests)
    (CHECK_ROOT / "unittest.log").write_text(stream.getvalue())
    summary = {"status": "passed" if result.wasSuccessful() else "failed", "tests_run": result.testsRun,
               "failures": len(result.failures), "errors": len(result.errors), "scope": "CPU mocks only; no GPU measurements",
               "measurement_status": "not_measured", "code_sha256": support.code_freeze()}
    support.write(CHECK_ROOT / "summary.json", summary)
    print(json.dumps(summary))
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    main()
