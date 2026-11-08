"""Verify saved keeper catalog preservation independently of failed HIP reads."""
import argparse
import hashlib
import json
from pathlib import Path


def load(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def review(run):
    raw = load(run / "summary.json")
    session = load(raw["source_session"])
    sources = session["models"]["B"]["sources"]
    checks, samples = {}, []
    refs = [run / "summary.json", Path(raw["source_session"])]
    snapshots = {}
    for stage in ("before", "after"):
        snapshots[stage] = []
        for rank in (0, 1):
            path = run / f"{stage}.worker_{rank}.json"
            refs.append(path)
            worker = load(path)
            snapshots[stage].append(worker)
            key = f"{stage}/worker{rank}"
            checks[key + "/identity"] = worker["worker"] == worker["gpu"] == rank and worker["node"] == "node27" and worker["session"] == "qwen_bootstrap_r01"
            checks[key + "/no_transactions"] = not worker["transactions"]
            checks[key + "/all_replicas_unleased"] = all(r["leases"] == 0 for r in worker["replicas"])
            source = next(s for s in sources if s["worker"] == rank)
            matches = [r for r in worker["replicas"] if r["id"] == source["id"]]
            checks[key + "/unique_full_base"] = len(matches) == 1
            if matches:
                replica = matches[0]
                checks[key + "/full_base_valid"] = replica["state"] == "VALID"
                for field in ("id", "identity", "tp", "off", "n", "tier", "meta", "index", "epoch", "generation", "state", "leases", "allocated_bytes"):
                    checks[key + "/base/" + field] = replica[field] == source[field]
            samples.append({"stage": stage, "gpu": rank, "hbm_headroom_bytes": worker["headroom_bytes"]["hbm"],
                            "headroom_sample_ns": worker["headroom_sample_ns"]})
        checks[stage + "/complete_base_bytes"] = sum(next(r["n"] for r in w["replicas"] if r["id"] in session["models"]["B"]["source_ids"]) for w in snapshots[stage]) == 70329718112
    for rank in (0, 1):
        before, after = snapshots["before"][rank], snapshots["after"][rank]
        # Headroom is a separate physical observation and varies with other owners.
        varying = {"headroom_bytes", "headroom_sample_ns"}
        checks[f"worker{rank}/full_catalog_metadata_identity_unchanged"] = {k:v for k,v in before.items() if k not in varying} == {k:v for k,v in after.items() if k not in varying}
        checks[f"worker{rank}/replica_set_unique"] = len({r["id"] for r in before["replicas"]}) == len(before["replicas"])
    return {"status": "passed" if all(checks.values()) else "failed", "checks": checks,
            "failed": [k for k, v in checks.items() if not v],
            "catalog_protection_confirmed": all(checks.values()), "full_base_bytes": 70329718112,
            "source_content_scope": "retained previously validated hash identity and full metadata/index; no weight content reread",
            "physical_headroom_observations": samples,
            "physical_free_recovery_verified": False, "complete_protected_postflight_verified": False,
            "parked_engine_postflight_verified": False, "node29_postflight_verified": False,
            "runtime_accepted": False, "performance_ready": False,
            "source_refs": [{"path": str(p.resolve()), "sha256": sha(p)} for p in refs],
            "gpu_operations": 0, "remote_operations": 0}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    result = review(args.run.resolve())
    (args.out / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    (args.out / "review_source.py").write_bytes(Path(__file__).read_bytes())
    (args.out / "manifest.json").write_text(json.dumps({"files": {p.name: sha(p) for p in args.out.iterdir() if p.is_file()},
                                                       "gpu_operations": 0, "remote_operations": 0}, indent=2) + "\n")
    print(json.dumps({"status": result["status"], "passed": sum(result["checks"].values()), "total": len(result["checks"]), "failed": result["failed"]}))
    if result["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
