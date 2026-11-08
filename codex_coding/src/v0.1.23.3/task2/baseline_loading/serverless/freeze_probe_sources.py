"""Preserve exact full-r02 importer sources by validating their recorded SHA."""
import argparse
import hashlib
import json
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--reference", type=Path, required=True)
p.add_argument("--out", type=Path, required=True)
a = p.parse_args()
a.out.mkdir(parents=True, exist_ok=False)
reference = json.loads(a.reference.read_text())["code_sha256_before"]
root = Path(__file__).resolve().parents[6]
adapter = root / "main_lib/fluidckpt/benchmarks/serverless_adapter.py"
text = adapter.read_text()
# Remove only the later trace-only instrumentation, then demand byte-identical recorded SHA.
text = text.replace("def hash_target(index, export, chunk_bytes=64 << 20, trace=None):", "def hash_target(index, export, chunk_bytes=64 << 20):")
text = text.replace("    for storage_index, (name, (_, size)) in enumerate(sorted(index.items(), key=lambda row: row[1][0])):",
                    "    for name, (_, size) in sorted(index.items(), key=lambda row: row[1][0]):")
blocks = [
    '        if trace:\n            trace({"event": "before_ipc_import", "storage_index": storage_index, "name": name,\n                   "device": row["handle"][0], "logical_bytes": size,\n                   "torch_cuda_initialized": torch.cuda.is_initialized()})\n',
    '        if trace:\n            trace({"event": "after_ipc_import", "storage_index": storage_index, "name": name})\n',
    '        if trace:\n            trace({"event": "storage_hashed_and_released", "storage_index": storage_index, "name": name})\n',
]
for block in blocks:
    text = text.replace(block, "")
copies = [(adapter, text.encode(), "trace-only instrumentation removed; exact recorded SHA required"),
          (Path(__file__).parent / "load_engine_storages.py", (Path(__file__).parent / "load_engine_storages.py").read_bytes(), "unchanged current file")]
records = []
for path, content, route in copies:
    actual = hashlib.sha256(content).hexdigest()
    if actual != reference[str(path)]:
        raise ValueError(f"source bytes do not match the full-r02 freeze: {path}")
    destination = a.out / path.name
    destination.write_bytes(content)
    records.append({"source": str(path), "copy": str(destination), "sha256": actual,
                    "matches_full_r02_freeze": True, "route": route})
(a.out / "source_manifest.json").write_text(json.dumps(records, indent=2))
print(json.dumps({"status": "passed", "source_before": str(a.out)}))
