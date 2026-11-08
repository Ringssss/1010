"""Check CPU subprocess bounds before the single read-only SSH collection."""
import argparse
import json
from pathlib import Path
import sys
import time

from collect_storage_env import bounded, statvfs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", choices=("exit", "sleep"))
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if args.fixture:
        if args.fixture == "sleep":
            time.sleep(2)
        print("cpu fixture")
        return
    args.out.mkdir(parents=True, exist_ok=False)
    script = str(Path(__file__).resolve())
    natural = bounded([sys.executable, script, "--fixture", "exit"], 2)
    timeout = bounded([sys.executable, script, "--fixture", "sleep"], 0.2)
    missing = statvfs("/fluidckpt_diagnosis30_nonexistent_fixture")
    checks = {"natural_exit": natural["returncode"] == 0 and natural["cleanup_proven"] and natural["signals"] == [],
              "timeout_exact_owned": timeout["timed_out"] and timeout["cleanup_proven"] and timeout["signals"] == ["SIGTERM"] and timeout["owner"]["pgid"] == timeout["owner"]["pid"],
              "missing_path_explicit": "error" in missing}
    result = {"checks": checks, "passed": all(checks.values()), "natural": natural, "timeout": timeout, "missing_path": missing, "gpu_operations": 0, "ssh_calls": 0}
    (args.out / "summary.json").write_text(json.dumps(result, indent=2)+"\n")
    print(json.dumps({"passed": result["passed"], "checks": checks}))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
