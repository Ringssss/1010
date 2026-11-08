"""Inspect the installed RCCL level parser without loading or running RCCL."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--container", required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    expected = "32aa13da8bbd4d9a4ec37be08014f0783f5f576c7439dc10a3e1c661aa35427a"
    # The address ranges below are specific to this frozen deployment binary.
    command = """sha256sum /opt/dtk/lib/librccl.so.1
strings -a -n 3 -t x /opt/dtk/lib/librccl.so.1 | grep -E 'NCCL_NET_GDR_LEVEL|^ *[0-9a-f]+ (LOC|PIX|PXB|PHB|SYS|NVB|DIS|PXN)$'
nm /opt/dtk/lib/librccl.so.1 2>/dev/null | grep -E 'ncclGetLevel|topoPathTypeStr|ncclTopoCheckGdr'
readelf -rW /opt/dtk/lib/librccl.so.1 | grep -E '000000000a59e8(00|08|10|18|20|28|30|38)'
objdump -s --start-address=0x18fd1 --stop-address=0x18fda /opt/dtk/lib/librccl.so.1
objdump -d -C --start-address=0xa4a4bd0 --stop-address=0xa4a4d89 /opt/dtk/lib/librccl.so.1
objdump -d -C --start-address=0xa4a5332 --stop-address=0xa4a5374 /opt/dtk/lib/librccl.so.1
objdump -d -C --start-address=0xa4a568c --stop-address=0xa4a5701 /opt/dtk/lib/librccl.so.1
"""
    result = {"gpu_operations": 0, "binary_expected_sha256": expected, "nodes": {}}
    for node in ("node27", "node29"):
        argv = ["docker", "exec", args.container, "bash", "-lc", command]
        if node != "node27":
            argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", node, shlex.join(argv)]
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=45)
        (args.out / f"{node}_binary.txt").write_text(proc.stdout)
        matched = proc.stdout.split()[0] == expected if proc.stdout else False
        result["nodes"][node] = {"argv": argv, "returncode": proc.returncode, "stderr": proc.stderr,
            "frozen_address_ranges_valid": matched, "output_sha256": hashlib.sha256(proc.stdout.encode()).hexdigest()}
        if not matched or proc.returncode:
            raise RuntimeError(f"{node}: frozen binary or read-only inspection failed")
    (args.out / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"out": str(args.out), "gpu_operations": 0, "nodes_passed": 2}))


if __name__ == "__main__":
    main()
