"""Frozen source, code and resource contracts for full Qwen Store runs."""
import hashlib
import json
from pathlib import Path


def sha_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(8 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def frozen_rank_hashes(path, model):
    baseline = json.loads(Path(path).read_text())
    case = baseline["cases"][0]
    verification = case["verification"]
    if not verification["passed"] or verification["checked_bytes"] != 70329718112:
        raise ValueError("S2 frozen source did not pass a complete SHA gate")
    sources = {row["tp"]: row for row in model["sources"]}
    result = {}
    for op in case["flow"]["plan"]["ops"]:
        rank = op["tp"]
        source = sources[rank]
        sha = verification["operations"][str(op["op_id"])]["source_sha256"]
        if op["off"] != 0 or op["n"] != source["n"] or op["identity"] != source["identity"]:
            raise ValueError("S2 operation is not the exact full frozen TP source")
        if rank in result:
            raise ValueError("S2 rank spans multiple operations; single payload SHA unavailable")
        result[rank] = sha
    if set(result) != {0, 1}:
        raise ValueError("frozen TP2 source hashes incomplete")
    return result


def freeze_files(root, extra=()):
    root = Path(root)
    directory = root / "codex_coding/src/v0.1.23.3/task2/baseline_loading/serverless"
    files = list(directory.glob("*.py"))
    files.append(root / "main_lib/fluidckpt/benchmarks/serverless_adapter.py")
    files.extend((root / "main_lib/fluidckpt/serving").glob("*.py"))
    files.append(root / "codex_coding/src/v0.1.23.3/task2/basic_scenarios/runtime_support.py")
    files.extend((root / "lib_cite/patches/serverlessllm").glob("*.patch"))
    files.extend((root / "lib_cite/ServerlessLLM-hip/sllm_store/sllm_store").glob("*.so"))
    files.extend((root / "lib_cite/ServerlessLLM-hip/sllm_store/csrc").rglob("*.cpp"))
    files.extend((root / "lib_cite/ServerlessLLM-hip/sllm_store/csrc").rglob("*.h"))
    files.append(root / "lib_cite/ServerlessLLM-hip/sllm_store/CMakeLists.txt")
    files.extend(Path(path) for path in extra)
    return {str(path): sha_file(path) for path in sorted(set(files))}


def check_prepared(prepared, model, frozen):
    if sorted(r["tp_rank"] for r in prepared["ranks"]) != [0, 1]:
        raise ValueError("prepared TP ranks must be unique 0 and 1")
    sources = {s["tp"]: s for s in model["sources"]}
    from fluidckpt.serving.layout import validate_tensor_layout
    for row in prepared["ranks"]:
        rank = row["tp_rank"]
        source = sources[rank]
        metadata = json.loads(Path(row["metadata"]).read_text())
        if row["payload_sha256"] != frozen[rank] or row["logical_bytes"] != source["n"]:
            raise ValueError("prepared payload does not match S2's frozen full-rank SHA")
        if metadata["index"] != source["index"]:
            raise ValueError("prepared packed storage offsets differ from frozen source")
        for key in ("model_identity", "tp_size", "tp_rank"):
            if metadata["meta"].get(key) != source["meta"].get(key):
                raise ValueError("prepared source identity differs from frozen source")
        validate_tensor_layout(source["meta"]["tensors"], metadata["meta"]["tensors"])
        if Path(row["data_file"]).stat().st_size != row["file_bytes"]:
            raise ValueError("prepared data size changed")


def parse_meminfo(text):
    return {line.split(":", 1)[0]: int(line.split()[1]) * 1024
            for line in text.splitlines() if len(line.split()) >= 2 and line.split()[1].isdigit()}
