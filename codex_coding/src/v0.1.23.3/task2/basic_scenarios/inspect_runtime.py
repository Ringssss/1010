"""Read-only retained service and GPU admission audit; never calls /health."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import resource
import time
from runtime_support import digest, host_command, http, load_state, new_run, node_command, select_base, write


def inspect(args):
    out = new_run(args.out)
    result = {"status": "running", "measurement_status": "not_measured", "read_only": True,
              "session": str(args.session.resolve()), "session_sha256": digest(args.session),
              "created_wall_time": time.time(), "endpoint_probes": [], "node_probes": {}, "errors": []}
    write(out / "summary.json", result)
    state = load_state(args.session)
    def probe(row):
        index, endpoint = row
        started = time.monotonic()
        try:
            data = http(endpoint, "inventory?compact=1", timeout=args.timeout)
            return {"worker": index, "endpoint": endpoint, "status": "passed", "inventory": data,
                    "elapsed_s": time.monotonic() - started}
        except Exception as exc:
            return {"worker": index, "endpoint": endpoint, "status": "failed", "error": repr(exc),
                    "elapsed_s": time.monotonic() - started}
    with ThreadPoolExecutor(len(state["endpoints"])) as pool:
        result["endpoint_probes"] = list(pool.map(probe, enumerate(state["endpoints"])))
    write(out / "summary.json", result)
    if args.nodes:
        def node_probe(node):
            commands = {"processes": ["ps", "-eo", "pid,pgid,args"],
                        "gpu_memory": ["hy-smi", "--showmeminfo", "vram", "--json"],
                        "tcp": ["cat", "/proc/net/tcp", "/proc/net/tcp6"]}
            probes = {key: node_command(node, state, command) for key, command in commands.items()}
            probes["container_state"] = host_command(node, ["docker", "inspect", state["nodes"][node]["container"],
                                                           "--format", "{{json .State}}"])
            probes["host_memory"] = host_command(node, ["cat", "/proc/meminfo"])
            probes["host_cpuowner_processes"] = host_command(node, ["pgrep", "-af", "fluidckpt.memory.owner"])
            if probes["gpu_memory"].get("returncode") != 0:
                probes["host_gpu_memory"] = host_command(node, ["hy-smi", "--showmeminfo", "vram", "--json"])
            return node, probes
        with ThreadPoolExecutor(len(args.nodes)) as pool:
            result["node_probes"] = dict(pool.map(node_probe, args.nodes))
    passed = [r["inventory"] for r in result["endpoint_probes"] if r["status"] == "passed"]
    failed = [r for r in result["endpoint_probes"] if r["status"] == "failed"]
    result["service_classification"] = {
        "live_keeper_count": len(passed), "failed_keeper_count": len(failed),
        "interpretation": "HTTP failure is recorded separately from Docker/SSH evidence; connection refusal alone does not prove service process absence"}
    model = state["models"]["B"]
    live_rows = [r for w in passed for r in w["replicas"] if r["state"] == "VALID"]
    available = {r["id"]: r for r in live_rows if r["id"] in set(model["source_ids"])}
    result["partial_retained_B"] = {"missing_source_ids": sorted(set(model["source_ids"])-set(available)),
        "available_bytes": sum(r["n"] for r in available.values()),
        "expected_bytes": sum(s["n"] for s in model["sources"]),
        "available_source_workers": sorted({r["worker"] for r in available.values()}),
        "scope": "accessible retained catalog metadata; unavailable owner contents not inferred"}
    try:
        if failed:
            raise RuntimeError(f"{len(failed)} retained endpoints unavailable")
        if [w["worker"] for w in passed] != list(range(len(passed))):
            raise ValueError("keeper identity order mismatch")
        if len({w["session"] for w in passed}) != 1 or any(w["transactions"] for w in passed):
            raise ValueError("mixed session or active transactions")
        replicas = [r for w in passed for r in w["replicas"] if r["state"] == "VALID"]
        base = select_base(state["models"]["B"], replicas)
        result["retained_B"] = {"source_ids": [r["id"] for r in base], "bytes": sum(r["n"] for r in base),
                                "rank_bytes": {str(s["tp"]): s["n"] for s in state["models"]["B"]["sources"]}}
        result["status"] = "passed"
    except Exception as exc:
        result["status"] = "failed"
        result["errors"].append(repr(exc))
    write(out / "summary.json", result)
    print(json.dumps({"status": result["status"], **result["service_classification"], "out": str(out)}))
    return result


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=5)
    parser.add_argument("--nodes", nargs="*", choices=("node27", "node28", "node29"), default=["node27", "node29"])
    args = parser.parse_args()
    if inspect(args)["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
