"""Print measured task2 outcomes from raw run results."""
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("run")
args = parser.parse_args()
path = Path(__file__).resolve().parents[3] / "codex_coding/results/task2" / args.run / "summary.json"
data = json.loads(path.read_text())
print("status:", data["status"], data.get("error", ""))
for phase, row in data.get("phases", {}).items():
    print(phase, {k: row[k] for k in ("ready_s", "capture_s", "skeleton_ready_s", "flow_s", "released_mib") if k in row})
    for reply in row.get("replies", []):
        print(" ", repr(reply["reply"]["text"]), "seconds:", round(reply["elapsed_s"], 3))
    if "verification" in row:
        print(" verification:", row["verification"])
