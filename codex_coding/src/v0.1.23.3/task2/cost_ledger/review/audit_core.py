"""Independent evidence checks; never import the producer's fit or ledger code."""
import hashlib
import json
from pathlib import Path


class Audit:
    def __init__(self):
        self.checks = []
        self.refs = []

    def check(self, name, passed, evidence=None):
        item = {"name": name, "passed": bool(passed)}
        if evidence is not None:
            item["evidence"] = evidence
        self.checks.append(item)
        return bool(passed)

    def read(self, path):
        path = Path(path).resolve()
        data = path.read_bytes()
        self.refs.append({"path": str(path), "sha256": hashlib.sha256(data).hexdigest()})
        return json.loads(data)

    def reference(self, item):
        path = Path(item["path"])
        self.check("frozen reference: " + str(path), path.is_file() and
                   hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"])

    def save(self, directory, scope, **extra):
        directory = Path(directory).resolve()
        directory.mkdir(parents=True, exist_ok=False)
        result = dict(passed=all(x["passed"] for x in self.checks), checks=self.checks,
                      refs=self.refs, scope=scope, GPU_launches=0, remote_calls=0, **extra)
        (directory / "qualification.json").write_text(json.dumps(result, indent=2) + "\n")
        return result


def inspect_partition(parent, children):
    """Check independently sampled parent containment and ordered child ownership.

    Exclusive children may leave gaps: gaps are observed unknown wall, not named
    causes. A partition constructed from common marks is reported separately from
    the independently sampled parent. No residual-as-child closes this check.
    """
    begin, end = parent["start_ns"], parent["end_ns"]
    domain = parent["clock_domain"]
    problems = []
    if not isinstance(begin, int) or not isinstance(end, int) or end < begin:
        problems.append("invalid parent timestamps")
    cursor = begin
    gaps = []
    measured = 0
    for child in children:
        start, stop = child["start_ns"], child["end_ns"]
        if child["clock_domain"] != domain:
            problems.append("clock domain mismatch")
        if not isinstance(start, int) or not isinstance(stop, int) or stop < start:
            problems.append("invalid child timestamps")
            continue
        if start < begin or stop > end:
            problems.append("child outside independently sampled parent")
        if start < cursor:
            problems.append("child overlap or reversed order")
        if start > cursor:
            gaps.append({"start_ns": cursor, "end_ns": start, "duration_ns": start-cursor})
        cursor = max(cursor, stop)
        measured += stop-start
    if cursor < end:
        gaps.append({"start_ns": cursor, "end_ns": end, "duration_ns": end-cursor})
    return {"passed": not problems, "problems": problems, "measured_child_ns": measured,
            "observed_unknown_gaps": gaps, "parent_ns": end-begin,
            "evidence_kind": "independent containment/order; arithmetic gap is not causal evidence"}


def expected_chunks(op_mib):
    return [min(32, size-pos) for size in op_mib for pos in range(0, size, 32)]


CONFIGS = {"L1": [32], "L2": [64], "L3": [32, 32], "L4": [48, 16],
           "L5": [96], "L3_repeat": [32, 32], "V1": [16, 16, 16],
           "V2": [40, 24, 16]}
CALIBRATION_ORDER = ["L3", "L1", "L4", "L2", "L5", "L3_repeat"]
HOLDOUT_ORDER = ["V2", "V1"]
