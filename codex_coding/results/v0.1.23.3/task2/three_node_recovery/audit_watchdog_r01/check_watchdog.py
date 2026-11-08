"""Exercise exact cleanup and registration failure on new CPU children only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import sys
from unittest.mock import patch

PROBE = Path(__file__).resolve().parents[1] / "probe"
sys.path.insert(0, str(PROBE))
import init_gate as gate


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out = args.out.resolve()
    if args.out.parent != gate.RESULT_ROOT or not args.out.name.startswith("audit_"):
        parser.error("require fresh versioned audit output")
    args.out.mkdir(parents=True, exist_ok=False)
    sources = []
    for path in (Path(__file__), PROBE / "init_gate.py", PROBE / "cpu_fixture.py"):
        raw = path.read_bytes()
        saved = args.out / path.name
        saved.write_bytes(raw)
        sources.append({"path": str(path.resolve()), "saved": str(saved), "sha256": hashlib.sha256(raw).hexdigest()})
    fixture = str(PROBE / "cpu_fixture.py")
    cases = {}
    timeout = args.out / "timeout"
    timeout.mkdir()
    cases["timeout"] = gate.bounded([sys.executable, fixture, "--delay", "5", "--ignore-term"], dict(os.environ), timeout, 0.25)
    registration = args.out / "registration_failure"
    registration.mkdir()
    original_write = gate.write

    def fail_registration(path, data):
        if path.name == "child.owner.json":
            raise OSError("independent CPU injected registration persistence failure")
        original_write(path, data)

    with patch.object(gate, "write", fail_registration):
        cases["registration_failure"] = gate.bounded([sys.executable, fixture, "--delay", "5"], dict(os.environ), registration, 1)
    checks = {
        "timeout_exact_term_kill": cases["timeout"]["timed_out"] and cases["timeout"]["cleanup"]["signals"] == ["TERM", "KILL"],
        "registration_failure_recorded": bool(cases["registration_failure"]["registration_error"]),
        "both_children_reaped": all(r["cleanup"]["passed"] and r["returncode"] is not None and not r["actual_after"]["live"] for r in cases.values()),
        "core_zero": list(resource.getrlimit(resource.RLIMIT_CORE)) == [0, 0],
        "torch_not_imported": "torch" not in sys.modules,
    }
    result = {"passed": all(checks.values()), "checks": checks, "cases": cases, "sources": sources, "gpu_operations": 0, "scope": "new synthetic CPU children; no old PID, GPU, SSH or container operations"}
    gate.write(args.out / "summary.json", result)
    print(json.dumps({"passed": result["passed"], "checks": checks}))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
