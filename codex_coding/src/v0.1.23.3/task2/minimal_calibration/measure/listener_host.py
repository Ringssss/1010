"""Bounded read-only check of a parent-selected rendezvous TCP listener."""
import argparse
import json
import os
from pathlib import Path
import resource
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--node", choices=("node28", "node30"), required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    expected = {"node28": ("m03r2n08", "5313e6f8-1712-45dc-9855-4c47358b6c4b"),
                "node30": ("m03r2n10", "d9d6a6fb-013f-49ac-98f9-e77eca67952e")}[args.node]
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if (os.uname().nodename, Path("/proc/sys/kernel/random/boot_id").read_text().strip()) != expected:
        raise ValueError("host identity differs")
    if not 1024 <= args.port <= 65535:
        raise ValueError("port outside user range")
    argv = ["ss", "-H", "-lnt", "sport = :" + str(args.port)]
    done = subprocess.run(argv, capture_output=True, timeout=3)
    print(json.dumps({"node": args.node, "port": args.port, "returncode": done.returncode,
                      "listener_free": done.returncode == 0 and not done.stdout.strip(),
                      "stdout": done.stdout.decode(), "stderr": done.stderr.decode(), "GPU_calls": 0}, indent=2))


if __name__ == "__main__":
    main()
