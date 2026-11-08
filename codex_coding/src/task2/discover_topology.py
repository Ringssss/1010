"""Read runtime topology and preserve the hardware identity evidence."""
import argparse
import json
from pathlib import Path
import resource
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "main_lib"))
from fluidckpt.topology import discover

if __name__ == "__main__":
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    result = discover()
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2))
    print(json.dumps({"fingerprint": result["fingerprint"], "gpus": result["gpus"]}, indent=2))
