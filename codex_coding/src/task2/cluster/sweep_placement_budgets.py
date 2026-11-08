"""Offline budget sensitivity only; no GPU experiments and no latency guarantees."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT/"main_lib"))
from fluidckpt.cluster.joint import joint_placement

parser = argparse.ArgumentParser(); parser.add_argument("--input", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
raw = args.input.read_bytes(); case = json.loads(raw)
rows = []
for hbm, dram in ((0, 0), (0, 2), (4, 0), (8, 0), (8, 2)):
    result = joint_placement(case["base"], case["candidates"], case["targets"], case["workers"],
                             case["profile"], {"hbm": hbm << 30, "dram": dram << 30}, case["location_budgets"])
    rows.append({"extra_hbm_gib": hbm, "extra_dram_gib": dram, **result})
report = {"input_sha256": hashlib.sha256(raw).hexdigest(),
          "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
          "scope": "predictions only; complete remote DRAM base is fixed; no extra hardware runs", "rows": rows}
args.output.write_text(json.dumps(report, indent=2))
print(json.dumps([{k: r[k] for k in ("extra_hbm_gib", "extra_dram_gib", "objective_s", "mip_gap")} for r in rows], indent=2))
