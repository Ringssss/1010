"""CPU checks for native evidence, cache probes, frozen sources, and admission."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import tempfile
import types
import sys
import unittest
from unittest.mock import patch

from inspect_baselines import (checkpoint_files, file_cache, new_output, parse_json_output, warm_files)
from native_loading import (DEFAULT_REFERENCE, DEFAULT_SESSION, ROOT, check_admission, frozen_reference,
                            prepare_spec, sha_value, install_native_measurement)
from runtime_support import digest, load_state
from complete_table import Inputs, normalize_case


class NativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.state = load_state(DEFAULT_SESSION)

    def setUp(self):
        self.args = argparse.Namespace(node="node29", port=33640, nccl_port=33740, memory_gib=128,
            guard_gib=12, model_path=Path("/data/node28/shared/zhujianian/Qwen3.6-35B-A3B"), cache_state="warm_file_cache")

    def test_real_full_rank_reference_matches_retained_layout(self):
        reference = frozen_reference(self.state, DEFAULT_REFERENCE)
        self.assertEqual(set(reference["ranks"]), {"0", "1"})
        for rank, row in reference["ranks"].items():
            self.assertEqual(row["bytes"], sum(s["nbytes"] for s in row["layout"]["storages"]))
            self.assertEqual(row["layout_sha256"], sha_value(row["layout"]))
            self.assertEqual(len(row["content_sha256"]), 64)

    def test_stale_reference_session_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "other_session.json"
            path.write_text("{}")
            with self.assertRaises(ValueError):
                frozen_reference(self.state, DEFAULT_REFERENCE, path)

    def test_real_packed_index_is_exact_native_storage_concatenation(self):
        path = ROOT / "codex_coding/results/v0.1.23.3/task2/baseline_loading/native_cpu_r01/registration_r02/expected.json"
        expected = json.loads(path.read_text())
        for source in self.state["models"]["B"]["sources"]:
            storages = expected["ranks"][str(source["tp"])]["layout"]["storages"]
            cursor = 0
            names = []
            for storage in storages:
                self.assertEqual(list(source["index"][storage["name"]]), [cursor, storage["nbytes"]])
                names.append(storage["name"])
                cursor += storage["nbytes"]
            self.assertEqual(set(names), set(source["index"]))
            self.assertEqual(len(names), len(source["index"]))
            self.assertEqual(cursor, source["n"])
            self.assertEqual(cursor, expected["ranks"][str(source["tp"])]["bytes"])

    def test_missing_rank_reference_is_rejected(self):
        state = copy.deepcopy(self.state)
        state["models"]["B"]["source_ids"].pop()
        with self.assertRaises(ValueError):
            frozen_reference(state, DEFAULT_REFERENCE)

    def test_fresh_launcher_does_not_install_restore_hooks(self):
        spec = prepare_spec(self.state, Path("/tmp/native-output"), self.args)
        self.assertIn("--engine", spec["command"])
        self.assertEqual(spec["command"][spec["command"].index("--load-format") + 1], "auto")
        self.assertEqual(spec["command"][spec["command"].index("--tp-size") + 1], "2")
        self.assertEqual({k for k in spec["env"] if k.startswith("FLUIDCKPT_")}, {"FLUIDCKPT_METRICS"})
        self.assertEqual(spec["env"]["HIP_VISIBLE_DEVICES"], "0,1")
        self.assertIn("--disable-cuda-graph", spec["command"])

    def test_min_core_on_owned_small_files_and_complete_warmup(self):
        with tempfile.TemporaryDirectory() as temporary:
            model = Path(temporary)
            sizes = [16384, 8192]
            for i, size in enumerate(sizes):
                (model / f"part{i}.safetensors").write_bytes(bytes([i]) * size)
            warmed = warm_files(model)
            cache = file_cache(model, samples_per_file=16)
            self.assertEqual(warmed["read_bytes"], sum(sizes))
            self.assertEqual(cache["checkpoint_bytes"], sum(sizes))
            self.assertEqual(cache["resident_fraction"], 1.0)
            self.assertEqual(cache["sampled_pages"], 6)

    def test_checkpoint_index_requires_every_shard(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            (path / "model.safetensors.index.json").write_text(json.dumps({"weight_map": {"x": "missing.safetensors"}}))
            with self.assertRaises(ValueError):
                checkpoint_files(path)

    def test_noisy_backend_stdout_preserves_json(self):
        self.assertEqual(parse_json_output('vendor startup log\n{"commit": "frozen"}\n'), {"commit": "frozen"})
        with self.assertRaises(ValueError):
            parse_json_output("only log text")

    def preflight(self):
        return {"probes": {"host_memory": {"stdout": "MemAvailable: 209715200 kB\n"},
            "gpu_memory": {"stdout": json.dumps({f"card{i}": {"vram Total Memory (MiB)": "65536", "vram Total Used Memory (MiB)": "4096"} for i in range(2)})},
            "tcp": {"stdout": "sl local_address rem_address st\n 0: 00000000:816F 00000000:0000 0A\n"}}}

    def test_native_admission_accepts_parked_background_and_capacity(self):
        admitted = check_admission(self.preflight(), self.state["models"]["B"], self.args)
        self.assertEqual(len(admitted["gpu_admission"]), 2)
        self.assertEqual(admitted["container_cap_bytes"], 128 << 30)

    def test_native_admission_rejects_capacity_and_port_collisions(self):
        for field, change in (("host_memory", {"stdout": "MemAvailable: 1024 kB\n"}),
                              ("gpu_memory", {"stdout": json.dumps({"card0": {"vram Total Memory (MiB)": "65536", "vram Total Used Memory (MiB)": "50000"}})}),
                              ("tcp", {"stdout": f"0: 00000000:{self.args.port:04X} 00000000:0000 0A\n"})):
            preflight = self.preflight()
            preflight["probes"][field] = change
            with self.subTest(field=field), self.assertRaises((MemoryError, ValueError)):
                check_admission(preflight, self.state["models"]["B"], self.args)

    def test_output_cannot_escape_or_reuse_results(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ValueError):
                new_output(Path(temporary) / "outside")

    def test_complete_table_accepts_native_clock_sha_and_exports(self):
        case = {"phase": "native", "method_id": "sglang_vendor_native", "adaptation_identity": "haiguang_vendor_sglang_native_checkpoint",
                "status": "passed", "correct": True, "primary_T_ready_s": 2.0,
                "verification": {"passed": True, "checked_bytes": 32}, "exports": [{"nbytes": 16}, {"nbytes": 16}],
                "timing": {"load_s": 2.0, "ranks": {str(r): {"events": [{"event": "load_begin", "host": "node29", "monotonic_ns": 100},
                    {"event": "weights_ready", "host": "node29", "monotonic_ns": 2000000100}]} for r in range(2)}}}
        run = {"scenario_id": "S2", "status": "passed", "cleanup_errors": [], "owned_remaining": []}
        with tempfile.TemporaryDirectory() as temporary:
            row = normalize_case(Inputs(temporary), "summary.json", run, case, 0)
        self.assertTrue(row["accepted"])
        self.assertEqual(row["method_id"], "sglang_vendor_native")
        self.assertEqual(row["metrics"]["T_ready_s"], 2.0)

    def test_original_loader_runs_before_ready_and_verification_runs_after(self):
        import native_loading as native
        sequence = []
        fake_loader = types.ModuleType("sglang.srt.model_loader.loader")
        class Model:
            def load_weights(self, weights):
                sequence.append("actual_model_load_weights")
                list(weights)
        class Loader:
            def __init__(self):
                self.load_config = argparse.Namespace(tp_rank=0, load_format="AUTO")
            def _get_all_weights(self):
                yield "weight", b"actual checkpoint bytes"
            def load_model(self, *args, **kwargs):
                sequence.append("actual_original_loader")
                model = fake_loader._initialize_model()
                model.load_weights(self._get_all_weights())
                return model
        class Runner:
            tp_rank = 0
            gpu_id = 0
            def load_model(self):
                self.model = Loader().load_model()
        fake_loader.DefaultModelLoader = Loader
        fake_loader._initialize_model = Model
        modules = {
            "torch": types.SimpleNamespace(cuda=types.SimpleNamespace(synchronize=lambda: sequence.append("synchronize"), memory_allocated=lambda: 16),
                                           distributed=types.SimpleNamespace(barrier=lambda **kwargs: sequence.append("post_ready_barrier"))),
            "sglang.srt.distributed": types.SimpleNamespace(get_tp_group=lambda: types.SimpleNamespace(cpu_group="fake_cpu_group")),
            "sglang.srt.model_loader": types.SimpleNamespace(loader=fake_loader),
            "sglang.srt.model_executor.model_runner": types.SimpleNamespace(ModelRunner=Runner)}
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary)
            (out / "expected.json").write_text(json.dumps({"ranks": {"0": {}}}))
            env = {"NATIVE_LOADING_EXPECTED": str(out / "expected.json"), "NATIVE_RUN_OUT": str(out),
                   "NATIVE_MODEL_PATH": temporary, "NATIVE_CACHE_STATE": "observed_file_cache"}
            def fingerprint(*args):
                sequence.append("fingerprint")
                return {"passed": True, "checked_bytes": 16}
            with patch.dict(sys.modules, modules), patch.dict("os.environ", env), \
                    patch.object(native, "backend_identity", return_value={"frozen": True}), \
                    patch.object(native, "file_cache", return_value={"resident_fraction": 1.0}), \
                    patch.object(native, "emit", side_effect=lambda rank, event, **kw: sequence.append(event)), \
                    patch.object(native, "model_fingerprint", side_effect=fingerprint):
                install_native_measurement()
                Runner().load_model()
        self.assertLess(sequence.index("load_begin"), sequence.index("actual_original_loader"))
        self.assertLess(sequence.index("actual_model_load_weights"), sequence.index("weights_ready"))
        self.assertLess(sequence.index("weights_ready"), sequence.index("fingerprint"))
        self.assertLess(sequence.index("weights_ready"), sequence.index("post_ready_barrier"))
        self.assertLess(sequence.index("post_ready_barrier"), sequence.index("fingerprint"))
        self.assertIn("native_weight_iterator_end", sequence)


if __name__ == "__main__":
    unittest.main()
