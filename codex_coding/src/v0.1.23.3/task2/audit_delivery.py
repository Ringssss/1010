"""Read-only audits of measured source versions and unchanged archive files; write versioned results only."""
import argparse
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[4]


def main(args):
    summary = json.loads((args.run_dir/"summary.json").read_text())
    analysis = json.loads((args.run_dir/"analysis.json").read_text())
    test_text = args.tests.read_text()
    matched = re.search(r"(\d+) passed", test_text)
    assert matched and " failed" not in test_text
    changed = [name for name, sha in summary["code_sha256"].items()
               if hashlib.sha256((ROOT/name).read_bytes()).hexdigest() != sha]
    archive = json.loads((ROOT/"codex_coding/results/task2/archive_20260929/check.json").read_text())
    archive_changed = [r["path"] for r in archive["files"]
                       if hashlib.sha256((ROOT/r["path"]).read_bytes()).hexdigest() != r["sha256"]]
    result = {"development": "v0.1.23.3", "task": "task2", "cpu_tests_passed": int(matched.group(1)),
              "gpu_run_passed": analysis["passed"], "current_source_matches_measured": not changed,
              "source_changes_after_run": changed, "archive_files_changed": archive_changed,
              "cross_node_validated": analysis.get("validation_topology", {}).get("cross_node_validated", False),
              "summary_sha256": hashlib.sha256((args.run_dir/"summary.json").read_bytes()).hexdigest(),
              "limitations": analysis["limitations"]}
    if args.local_dir:
        local = json.loads((args.local_dir/"summary.json").read_text())
        result["earlier_local_validation"] = {"status": local["status"], "source_changes_after_that_run":
            [name for name, sha in local["code_sha256"].items() if hashlib.sha256((ROOT/name).read_bytes()).hexdigest() != sha],
            "note": "post-local-run changes added GPU-alias validation, explicit replanning, verified network/build-route admission and capacity-aware initial NUMA binding; CPU-tested and included in the later run"}
    result["passed"] = bool(analysis["passed"] and not changed and not archive_changed)
    (args.run_dir/"delivery.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["passed"]: raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--local-dir", type=Path); parser.add_argument("--tests", type=Path, required=True)
    main(parser.parse_args())
