"""Inspect build dependencies without opening or allocating any GPU."""
import importlib.metadata
import argparse
import importlib
import json
import os
import shutil
import sys
import torch

p = argparse.ArgumentParser()
p.add_argument("--source-checkout")
a = p.parse_args()
extensions = {}
if a.source_checkout:
    sys.path.insert(0, a.source_checkout + "/sllm_store")
    for name in ("sllm_store._C", "sllm_store._checkpoint_store"):
        module = importlib.import_module(name)
        extensions[name] = module.__file__

packages = {}
for name in ("torch", "cmake", "ninja", "grpcio", "protobuf", "setuptools"):
    try:
        packages[name] = importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        packages[name] = None
print(json.dumps({"packages": packages, "torch_hip": torch.version.hip,
    "torch_cmake": torch.utils.cmake_prefix_path, "torch_file": torch.__file__,
    "rocm_home": os.getenv("ROCM_HOME"), "hipcc": shutil.which("hipcc"),
    "cmake": shutil.which("cmake"), "ninja": shutil.which("ninja"), "extensions": extensions}, indent=2))
