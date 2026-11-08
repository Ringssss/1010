"""Exercise reviewer rejection paths with CPU-only adversarial evidence."""
import argparse
import copy
import json
from pathlib import Path

from audit_core import Audit, CALIBRATION_ORDER, CONFIGS, expected_chunks, inspect_partition
from audit_numeric import reconstruct, serializable


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    audit = Audit()
    parent = {"start_ns": 10, "end_ns": 100, "clock_domain": "cpu-test"}
    children = [{"start_ns": 20, "end_ns": 50, "clock_domain": "cpu-test"},
                {"start_ns": 50, "end_ns": 90, "clock_domain": "cpu-test"}]
    good = inspect_partition(parent, children)
    audit.check("valid disjoint children preserve two independently observed parent gaps",
                good["passed"] and [x["duration_ns"] for x in good["observed_unknown_gaps"]] == [10, 10])
    for title, field, value in (("overlap", "start_ns", 49),
                                ("cross-clock", "clock_domain", "another"),
                                ("parent escape", "end_ns", 101),
                                ("negative duration", "end_ns", 49)):
        changed = copy.deepcopy(children)
        changed[1][field] = value
        audit.check("reject " + title, not inspect_partition(parent, changed)["passed"])
    samples = []
    for config in CALIBRATION_ORDER:
        sizes = CONFIGS[config]
        value = 100000 + 2000*sum(sizes) + 100000*len(sizes) + 100000*len(expected_chunks(sizes))
        samples.append({"config_id": config, "measured": {"service_ns": value, "worker_ns": value+10000,
            "controller_execute_ns": value+1000000, "full_filled_ns": value+10000000}})
    observer = {"clock_extra_p95_ns": 1000, "dummy_log_scale_ns": 1000}
    fit = reconstruct(samples, observer)
    audit.check("recover unclamped four coefficients exactly from five declared training points",
                fit["service_ns"]["candidates"]["a_bN_cK_dC"]["raw_coefficients"]
                == [100000, 2000, 100000, 100000])
    audit.check("independent raw signal/drift gates admit known four-term case",
                fit["service_ns"]["candidates"]["a_bN_cK_dC"]["admitted"])
    changed = copy.deepcopy(samples)
    for key in changed[-1]["measured"]:
        changed[-1]["measured"][key] += 1000000
    drift = reconstruct(changed, observer)
    audit.check("anchor is excluded from fit yet rejects drift", not drift["worker_ns"]["drift_passed"]
                and drift["service_ns"]["candidates"]["a_bN_cK_dC"]["raw_coefficients"]
                == fit["service_ns"]["candidates"]["a_bN_cK_dC"]["raw_coefficients"])
    outcome = audit.save(args.output, "Reviewer CPU rejection-path tests; generated numbers are not hardware evidence")
    (args.output / "independent_synthetic_reconstruction.json").write_text(json.dumps(serializable(fit), indent=2)+"\n")
    print(json.dumps({"passed": outcome["passed"], "checks": len(audit.checks)}))
    if not outcome["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
