"""CPU contracts for a bounded bridge through the retained keeper executor."""
import copy

SCHEMA = "keeper_bridge.v1"
CASES = {"single_A": (0,), "single_B": (1,), "pair": (0, 1)}


def targets_for(model, run_id, case, nbytes, off=0):
    if case not in CASES or type(nbytes) is not int or nbytes <= 0:
        raise ValueError("invalid bridge case or byte count")
    if type(off) is not int or off < 0 or not run_id.startswith("bridge_") or "/" in run_id:
        raise ValueError("invalid extent or run ownership namespace")
    sources = {s["tp"]: s for s in model["sources"]}
    result = []
    for rank in CASES[case]:
        source = sources[rank]
        if source["worker"] != rank or source["tier"] != "dram" or off+nbytes > source["n"]:
            raise ValueError("bridge must read exact retained DRAM rank on worker 0/1")
        cursor = off
        for start, length in sorted(source["index"].values()):
            if start+length <= cursor:
                continue
            if start > cursor:
                break
            cursor = min(off+nbytes, start+length)
            if cursor == off+nbytes:
                break
        if cursor != off+nbytes:
            raise ValueError("extent is not covered by the retained manifest")
        rid = f"{run_id}/{case}/rank{rank}"
        result.append(dict(key=rid, id=rid, mode="replica", worker=rank+2, tier="hbm",
            identity=source["identity"], tp=rank, off=off, n=nbytes,
            meta=source["meta"], index=source["index"]))
    return result


def lease_check(workers, source_ids, active):
    entries = {r["id"]: r for w in workers for r in w["replicas"]}
    return all(entries[rid]["state"] == "VALID" and
               entries[rid].get("leases") == int(active) for rid in source_ids)


def protected_workers(workers, run_id):
    result = copy.deepcopy(workers)
    for worker in result:
        owned = [r for r in worker["replicas"] if r["id"].startswith(run_id+"/")]
        if any(r["state"] != "RELEASED" or r.get("leases", 0) for r in owned):
            raise ValueError("owned target was not physically retired from the keeper catalog")
        worker["replicas"] = [r for r in worker["replicas"] if not r["id"].startswith(run_id+"/")]
    return result


def rpc_window(rows, receivers, domain):
    selected = [r for r in rows if r["worker"] in receivers]
    if len(selected) != len(receivers) or any(r["clock_domain"] != domain or
            r["end_ns"] < r["begin_ns"] for r in selected):
        raise ValueError("missing receiver RPC or mixed host monotonic domains")
    begin = min(r["begin_ns"] for r in selected)
    end = max(r["end_ns"] for r in selected)
    overlap = max(0, min(r["end_ns"] for r in selected)-max(r["begin_ns"] for r in selected))
    return {"begin_ns": begin, "end_ns": end, "elapsed_s": (end-begin)/1e9,
            "clock_domain": domain, "receiver_workers": receivers,
            "overlap_s": overlap/1e9 if len(selected) > 1 else None,
            "scope": "receiver execute HTTP envelopes on target-host coordinator; includes dispatch and response",
            "worker_true_monotonic_window": None, "hardware_shared_resource_attribution": False}
