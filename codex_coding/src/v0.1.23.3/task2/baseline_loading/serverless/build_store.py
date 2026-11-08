"""Build upstream HIP extensions in the dedicated CPU-only container."""
import argparse
import json
from pathlib import Path
import subprocess
import time

p = argparse.ArgumentParser()
p.add_argument("--container", default="zjn-serverless-build-r01")
p.add_argument("--repo", type=Path, default=Path.cwd())
p.add_argument("--out", type=Path, required=True)
a = p.parse_args()
source = a.repo / "lib_cite/ServerlessLLM-hip/sllm_store"
a.out.mkdir(parents=True, exist_ok=True)
env = ["env", "PYTORCH_ROCM_ARCH=gfx936", "CMAKE_INCLUDE_PATH=/opt/dtk/.hyhal/include",
       "LD_LIBRARY_PATH=/opt/dtk/dcc/gcvm/lib:/opt/dtk/llvm/lib:/opt/dtk/lib:/opt/dtk/hip/lib:/opt/dtk/.hyhal/lib:/opt/dtk/.hyhal/lib64:/opt/dtk/.hyhal/rocm_smi/lib"]
commands = [
    ["cmake", "-S", str(source), "-B", str(a.out / "build"), "-G", "Ninja",
     "-DSLLM_STORE_PYTHON_EXECUTABLE=/usr/bin/python3", "-DCMAKE_HIP_ARCHITECTURES=gfx936",
     "-DSLLM_USE_SYSTEM_GLOG=ON", "-DCMAKE_PREFIX_PATH=/opt/dtk;/opt/dtk/.hyhal",
     "-Dhip_DIR=/opt/dtk/lib/cmake/hip", "-DCMAKE_HIP_COMPILER=/opt/dtk/llvm/bin/clang++",
     "-DCMAKE_HIP_COMPILER_ROCM_ROOT=/opt/dtk", "-DCMAKE_BUILD_TYPE=Release",
     "-DCMAKE_CXX_FLAGS=-I/opt/dtk/.hyhal/rocm_smi/include -I/opt/dtk/.hyhal/include",
     "-DCMAKE_LIBRARY_OUTPUT_DIRECTORY=" + str(source / "sllm_store")],
    ["cmake", "--build", str(a.out / "build"), "--target", "_C", "_checkpoint_store", "-j", "2"],
]
records = []
attempt = time.time_ns()
command_file = a.out / f"build_commands_{attempt}.json"
for index, command in enumerate(commands):
    argv = ["docker", "exec", a.container, *env, *command]
    with (a.out / f"build_{attempt}_stage{index}.log").open("w") as log:
        result = subprocess.run(argv, stdout=log, stderr=subprocess.STDOUT)
    records.append({"argv": argv, "returncode": result.returncode})
    command_file.write_text(json.dumps(records, indent=2))
    if result.returncode:
        raise SystemExit(result.returncode)
