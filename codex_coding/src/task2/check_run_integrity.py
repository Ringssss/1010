"""Check recorded code fingerprints, sample completion and full-byte verification coverage."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
parser = argparse.ArgumentParser()
parser.add_argument("--run-dir", required=True)
args = parser.parse_args()
directory = Path(args.run_dir)
data = json.loads((directory / "summary.json").read_text())
mismatches = [name for name, expected in data["code_sha256"].items()
              if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected]
samples = data["samples"]
restores = [s for s in samples if s["mode"] == "restore"]
checks = [row for s in restores for row in s["verification"]]
result = {"run": data["run"], "recorded_source_files": len(data["code_sha256"]),
          "source_hash_mismatches": mismatches, "samples": len(samples),
          "all_samples_passed": all(s.get("passed") for s in samples),
          "restore_samples": len(restores), "verified_bytes_including_repeats": sum(c["checked_bytes"] for c in checks),
          "all_bytes_equal": all(c["passed"] and c["mismatched_bytes"] == 0
                                 and c["checked_bytes"] == c["expected_bytes"] for c in checks)}
result["passed"] = (data["status"] == "passed" and not mismatches and result["all_samples_passed"]
                    and bool(restores) and result["all_bytes_equal"])
(directory / "integrity.json").write_text(json.dumps(result, indent=2))
print(json.dumps(result, indent=2))
if not result["passed"]:
    raise SystemExit(1)
