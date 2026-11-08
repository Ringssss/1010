"""Measure one controlled export-publication delay per polling interval, without GPU work."""
import argparse
import json
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "main_lib"))
from fluidckpt.serving.handoff import wait_for_exports

parser = argparse.ArgumentParser(); parser.add_argument("--out", required=True)
args = parser.parse_args()
output = Path(args.out); output.parent.mkdir(parents=True, exist_ok=True)
rows = []
for interval in (0.5, 0.01):
    directory = output.parent / f"handoff_{interval}"
    directory.mkdir(exist_ok=False)
    stamps = {}
    def publish():
        time.sleep(0.05)
        for rank in (0, 1):
            temp = directory / f"rank{rank}.tmp"
            temp.write_bytes(b"fixture")
            temp.replace(directory / f"rank{rank}.pkl")
        stamps["published"] = time.monotonic()
    writer = threading.Thread(target=publish); writer.start()
    detail = wait_for_exports(directory, timeout=2, poll_s=interval)
    detected = time.monotonic(); writer.join()
    rows.append(dict(detail, detection_delay_s=max(0, detected-stamps["published"])))
failed = False
try: wait_for_exports(output.parent / "missing", timeout=0.02)
except TimeoutError: failed = True
result = {"scope": "controlled CPU handoff publication; not a model speedup measurement", "rows": rows,
          "missing_export_rejected": failed, "passed": failed and rows[1]["detection_delay_s"] < rows[0]["detection_delay_s"]}
output.write_text(json.dumps(result, indent=2)); print(json.dumps(result, indent=2))
if not result["passed"]: raise SystemExit(1)
