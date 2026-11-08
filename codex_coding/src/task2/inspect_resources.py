"""Record host memory of only the Keeper processes belonging to one task2 run."""
import argparse
import json
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parents[3]
parser = argparse.ArgumentParser()
parser.add_argument("--run", required=True)
args = parser.parse_args()
out = ROOT / "codex_coding/results/task2" / args.run
rows = []
for process in Path("/proc").iterdir():
    if not process.name.isdecimal():
        continue
    try:
        command = (process / "cmdline").read_bytes().decode().split("\0")
        if "fluidckpt.serving.keeper" not in command or str(out / "handoff") not in command:
            continue
        memory = {}
        for line in (process / "smaps_rollup").read_text().splitlines()[1:]:
            key, value = line.split(":", 1)
            memory[key] = int(value.split()[0]) * 1024
        rows.append({"pid": int(process.name), "gpu": command[command.index("--gpu") + 1],
                     "memory_bytes": memory})
    except (FileNotFoundError, ProcessLookupError):
        continue
result = {"timestamp": time.time(), "keepers": rows,
          "total_pss_GiB": sum(r["memory_bytes"]["Pss"] for r in rows) / 2**30}
if len(rows) != 2:
    raise RuntimeError(f"expected two running Keepers, found {len(rows)}")
with open(out / "host_memory.jsonl", "a") as stream:
    stream.write(json.dumps(result) + "\n")
print(json.dumps(result, indent=2))
