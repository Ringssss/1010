"""CPU-only validation of control isolation and executable environment unsetting."""
import argparse
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import rank_entry
import run_probe as driver

OUT = None


def args(mode="xml", stage="measurement"):
    return argparse.Namespace(hca="shca_0", flow_b_hca="shca_0", ifname="ib0", port=34010,
        timeout=120, topology_mode=mode, xml=Path("/usr/local/built-in-508-topo-input-tj-default.xml"),
        stage=stage, matrix="network", reverse=False)


WORKERS = [{"worker": rank, "node": "node27" if rank < 2 else "node29",
            "gpu": rank % 2, "numa": rank % 2} for rank in range(4)]
STATE = {"env": {"NCCL_TOPO_FILE": "/inherited/wrong.xml", "NCCL_NET_GDR_LEVEL": "PHB"},
         "nodes": {"node27": {"ip": "12.11.3.27"}}}


class ProbeTests(unittest.TestCase):
    def make_specs(self, mode):
        directory = OUT / mode
        directory.mkdir()
        return [json.loads(Path(row["spec"]).read_text())
                for row in driver.process_specs(STATE, WORKERS, directory, args(mode))]

    def test_matched_controls_and_unique_dump(self):
        xml, unset = self.make_specs("xml"), self.make_specs("unset")
        for rank, (a, b) in enumerate(zip(xml, unset)):
            self.assertEqual(b["command"][:3], ["env", "-u", "NCCL_TOPO_FILE"])
            self.assertNotIn("NCCL_TOPO_FILE", b["env"])
            self.assertEqual(a["env"]["NCCL_TOPO_FILE"], str(args().xml))
            ignored = {"NCCL_TOPO_FILE", "NCCL_TOPO_DUMP_FILE", "NCCL_DEBUG_FILE"}
            self.assertEqual({k: v for k, v in a["env"].items() if k not in ignored},
                             {k: v for k, v in b["env"].items() if k not in ignored})
            self.assertEqual(a["env"]["NCCL_TOPO_DUMP_FILE_RANK"], str(0 if rank < 2 else 1))
            self.assertEqual(a["env"]["NCCL_IB_HCA"], "shca_0")
            self.assertEqual(a["core_dump_limit"], 0)
        self.assertEqual(len({row["env"]["NCCL_TOPO_DUMP_FILE"] for row in xml}), 4)

    def test_exec_unset_despite_process_environment_merge(self):
        parent = dict(os.environ, NCCL_TOPO_FILE="/parent/wrong.xml", PROBE_SECRET="hidden")
        process = [sys.executable, str(Path(__file__).with_name("cpu_environment.py"))]
        explicit = subprocess.run(["env", "-u", "NCCL_TOPO_FILE", *process], env=parent,
                                  check=True, capture_output=True, text=True)
        data = json.loads(explicit.stdout)
        self.assertNotIn("NCCL_TOPO_FILE", data["environment"])
        self.assertNotIn("PROBE_SECRET", data["environment"])
        self.assertFalse(data["torch_imported"])
        inherited = subprocess.run(process, env=parent, check=True, capture_output=True, text=True)
        self.assertEqual(json.loads(inherited.stdout)["environment"]["NCCL_TOPO_FILE"], "/parent/wrong.xml")
        (OUT / "environment_inheritance.json").write_text(json.dumps({"unset": data,
            "inherited": json.loads(inherited.stdout)}, indent=2))

    def test_formal_and_startup_protocol(self):
        driver.configure(args())
        formal = driver.base.protocol(WORKERS, "session-sha", "network", False)
        self.assertEqual([c["case_id"] for c in formal["cases"]],
            ["network_single_a", "network_single_b", "network_pair"])
        for case in formal["cases"]:
            self.assertEqual((case["buffer_bytes"], case["iterations"], case["bytes_per_flow"]),
                             (256 << 20, 16, 4 << 30))
        driver.configure(args(stage="startup"))
        startup = driver.base.protocol(WORKERS, "session-sha", "network", False)
        self.assertEqual(len(startup["cases"]), 1)
        self.assertEqual(startup["cases"][0]["bytes_per_flow"], 1 << 20)
        self.assertEqual(startup["samples_per_configuration"], 0)
        self.assertTrue(startup["startup_only"])
        (OUT / "protocols.json").write_text(json.dumps({"formal": formal, "startup": startup}, indent=2))

    def test_dump_parse_and_missing_graph_not_fabricated(self):
        shape = driver.topology_shape(b'<system><cpu numaid="1"><pci><gpu dev="1"/></pci><nic><net name="shca_0"/></nic></cpu></system>')
        self.assertEqual(shape["gpu_count"], 1)
        self.assertEqual(shape["cpu_nodes"][0]["numaid"], "1")
        self.assertEqual(shape["net_attributes"][0]["name"], "shca_0")
        with self.assertRaises(Exception):
            driver.topology_shape(b"invalid xml")

    def test_environment_excludes_unrelated_secrets(self):
        with patch.dict(os.environ, {"NCCL_TEST_CONTROL": "yes", "SECRET_TOKEN": "excluded"}):
            self.assertEqual(rank_entry.environment()["NCCL_TEST_CONTROL"], "yes")
            self.assertNotIn("SECRET_TOKEN", rank_entry.environment())

    def test_exact_own_pid_validation(self):
        expected = {"pid": 17, "start_time": "100", "spec": "own"}
        self.assertTrue(driver.validate_process_exit(expected, expected, {"pid": 17, "live": False}))
        for actual in ({"pid": 17, "live": True}, {"pid": 17, "live": False, "start_time": "101"},
                       {"pid": 18, "live": False}):
            with self.assertRaises(ValueError):
                driver.validate_process_exit(expected, expected, actual)

    def test_postmeasurement_guard_before_cleanup(self):
        directory = OUT / "guard"
        directory.mkdir()
        count = [0]
        def complete(_):
            count[0] += 1
            for rank in range(4):
                (directory / f"rank_{rank}.libraries.json").write_text("{}")
        with patch.object(driver.time, "sleep", complete), patch.object(driver.previous, "BASE_COLLECT",
                return_value=([{"case_id": "test"}], [], [])):
            result = driver.deferred_collect(directory, {}, "shca_0", "ib0")
        self.assertEqual(count[0], 1)
        self.assertEqual(result[0][0]["case_id"], "test")
        self.assertEqual(result[1], [])

    def test_no_raw_run_reuse(self):
        owned = OUT / "probe_no_reuse"
        driver.new_out(owned)
        with self.assertRaises(FileExistsError):
            driver.new_out(owned)
        with self.assertRaises(ValueError):
            driver.new_out(OUT / "invalid_name")


def main():
    global OUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parsed = parser.parse_args()
    OUT = parsed.out.resolve()
    allowed = driver.RESULT_ROOT / "probe_cpu_r01"
    if OUT != allowed and allowed not in OUT.parents:
        parser.error("CPU output is restricted to probe_cpu_r01")
    OUT.mkdir(parents=True, exist_ok=False)
    stream = io.StringIO()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ProbeTests)
    with redirect_stdout(stream):
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    (OUT / "test.log").write_text(stream.getvalue())
    summary = {"status": "passed" if result.wasSuccessful() else "failed", "tests": result.testsRun,
        "failures": len(result.failures), "errors": len(result.errors), "gpu_launches": 0,
        "command": sys.argv, "source_sha256": driver.frozen_code()}
    driver.inventory.write(OUT / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False))
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    main()
