"""CPU checks that Blitz drift detection follows execution dependencies only."""
import argparse
import io
import json
from pathlib import Path
import tempfile
import unittest

from blitz_freeze import ROOT, SEEDS, code_freeze, project_imports


class FreezeTests(unittest.TestCase):
    snapshot = None

    def test_real_backend_and_lifecycle_dependencies_are_frozen(self):
        required = {
            "main_lib/fluidckpt/benchmarks/blitz_adapter.py",
            "main_lib/fluidckpt/cluster/control.py", "main_lib/fluidckpt/cluster/catalog.py",
            "main_lib/fluidckpt/cluster/worker.py", "main_lib/fluidckpt/cluster/wire.py",
            "main_lib/fluidckpt/cluster/planning.py", "main_lib/fluidckpt/executor/chunks.py",
            "main_lib/fluidckpt/serving/keeper.py", "main_lib/fluidckpt/serving/lifecycle.py",
            "main_lib/fluidckpt/serving/sglang_hook.py", "main_lib/fluidckpt/serving/ipc_attach.py",
            "main_lib/fluidckpt/serving/ipc_layout.py", "main_lib/fluidckpt/serving/layout.py",
            "main_lib/fluidckpt/serving/measure.py", "main_lib/fluidckpt/memory/host.py",
            "codex_coding/src/v0.1.23.3/task2/basic_scenarios/run_scenarios.py",
            "codex_coding/src/v0.1.23.3/task2/basic_scenarios/runtime_support.py",
        }
        self.assertTrue(required <= set(self.snapshot), sorted(required-set(self.snapshot)))
        self.assertTrue(set(SEEDS) <= set(self.snapshot))

    def test_unrelated_baselines_and_experiment_scripts_are_not_frozen(self):
        excluded = {
            "main_lib/fluidckpt/benchmarks/serverless_adapter.py",
            "main_lib/fluidckpt/benchmarks/baseline_scope.py",
            "codex_coding/src/v0.1.23.3/task2/paper_e1_e8/runtime/restore_engine.py",
            "codex_coding/src/task2/cluster/run_system.py",
            "codex_coding/src/v0.1.23.3/task2/basic_scenarios/test_contracts.py",
            "codex_coding/src/v0.1.23.3/task2/baseline_loading/blitz/test_blitz.py",
            "codex_coding/src/v0.1.23.3/task2/baseline_loading/blitz/test_freeze.py",
        }
        self.assertFalse(excluded & set(self.snapshot), sorted(excluded & set(self.snapshot)))

    def test_upstream_cache_source_TP_and_feature_origins_are_frozen(self):
        for checkout in ("blitz-scale", "blitz-scale-hip"):
            for suffix in ("router_v2/src/replica/steersman/mod.rs",
                           "router_v2/src/replica/cybernetics/exec_blitz.rs", "src/service/service.cc",
                           "router_v2/Cargo.toml"):
                self.assertIn(f"lib_cite/{checkout}/{suffix}", self.snapshot)

    def test_relative_package_and_lazy_import_closure_without_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "main_lib/fluidckpt/cluster"
            package.mkdir(parents=True)
            for path in (root / "main_lib/fluidckpt/__init__.py", package / "__init__.py",
                         package / "catalog.py", package / "wire.py"):
                path.write_text("raise AssertionError('must not import fixture code')\n")
            source = package / "worker.py"
            source.write_text("from .catalog import Catalog\ndef lazy():\n    from fluidckpt.cluster import wire\n")
            paths = {str(p.relative_to(root)) for p in project_imports(source, root)}
            self.assertTrue({"main_lib/fluidckpt/__init__.py", "main_lib/fluidckpt/cluster/__init__.py",
                             "main_lib/fluidckpt/cluster/catalog.py", "main_lib/fluidckpt/cluster/wire.py"} <= paths)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    FreezeTests.snapshot = code_freeze()
    log = io.StringIO()
    result = unittest.TextTestRunner(stream=log, verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(FreezeTests))
    (args.out / "tests.log").write_text(log.getvalue())
    report = {"passed": result.wasSuccessful(), "tests": result.testsRun, "frozen_files": len(FreezeTests.snapshot),
              "scope": "CPU dependency selection; no GPU or worker RPC", "code_sha256": FreezeTests.snapshot}
    (args.out / "tests.json").write_text(json.dumps(report, indent=2)+"\n")
    print(log.getvalue())
    print(json.dumps({"passed": report["passed"], "frozen_files": report["frozen_files"]}))
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    main()
