"""Fetch chunks into one node from its holders, one source at a time.

Concurrent triples of RCCL processes were unreliable here (empty logs, processes that never
reached their warm broadcast), so this moves the same bytes pair by pair and keeps the numbers
honest: the total wall time is the sum, and the per-pair rate is what each source actually
achieved. Parallelism can be added back once a single pair is trustworthy under load.

Chunks already present locally are skipped, so this is a real partial-replica restore.

Usage (on the destination node):
  python3 fetch_serial.py --work <dir> --dst node27 --dst-card 4 --sources node28=0,node29=1
"""
import argparse
import json
import os
import subprocess
import sys
import time

REPO = "/home/zhujianian/zhujianian/fluidckpt_zjn"
SCRIPT = f"{REPO}/codex_coding/src/v0.1.23.3/task2/live_graph/gpu/chunk_fluid.py"
IP = {"node27": "12.11.3.27", "node28": "12.11.3.28",
      "node29": "12.11.3.29", "node30": "12.11.3.30"}


def sh(node, cmd, timeout=1800):
    return subprocess.run(["ssh", "-n", "-o", "BatchMode=yes", node, cmd],
                          capture_output=True, text=True, timeout=timeout)


def local_chunks(node, store="/root/chunkstore"):
    r = sh(node, f"docker exec zjn-fluid-live-r01-{node[4:]} bash -lc "
                 f"'find {store} -type f -not -name \"*.tmp\" -printf \"%f\\n\"'")
    return {x.strip() for x in (r.stdout or "").splitlines() if x.strip()}


def have_chunk(node, cid, store="/root/chunkstore"):
    r = sh(node, f"docker exec zjn-fluid-live-r01-{node[4:]} bash -lc "
                 f"'test -f {store}/{cid[:2]}/{cid} && echo yes || echo no'")
    return "yes" in (r.stdout or "")


def launch(node, role, card, chunks_path, master, ack, peer_ip, pair_dir):
    # Expose a four-card window and address the target card as ordinal 0 inside it. A container
    # that exposes a SINGLE card hangs in the first real collective on this stack; every run that
    # completed (restore_chunks.py, pair_bcast.py) used a multi-card visible set with --gpu 0.
    # The gather is cheap and this removes the last asymmetry between the working and failing runs.
    c = int(card)
    base = (c // 4) * 4
    vis = ",".join(str(base + i) for i in range(4))
    cmd = (f"docker exec -d zjn-fluid-live-r01-{node[4:]} bash -lc '"
           f"cd {REPO}; export PYTHONPATH=main_lib HIP_VISIBLE_DEVICES={vis}; "
           f"LD_LIBRARY_PATH=/opt/hyhal/lib:$LD_LIBRARY_PATH; "
           f"export NCCL_NET_PLUGIN=librccl-net-shca.so NCCL_SOCKET_IFNAME=ib0 NCCL_DEBUG=WARN; "
           f"ulimit -c 0; "
           f"python3 {SCRIPT} {role} --store /root/chunkstore --chunks {chunks_path} "
           f"--out {pair_dir} --master {master} --ack-port {ack} --peer-ip {peer_ip} --gpu 0 "
           f"--wait-mode {os.environ.get('WAIT_MODE', 'device')} "
           f"--chunk-mib {os.environ.get('CHUNK_MIB', '64')} "
           f"{'--persist ' if os.environ.get('PERSIST') == '1' else ''}"
           f"{'--probe-sync ' if os.environ.get('PROBE_SYNC') == '1' else ''}"
           f"{'--prefault ' if os.environ.get('PREFAULT') == '1' else ''}"
           f"{'--source-pool ' if os.environ.get('SOURCE_POOL') == '1' and role == 'push' else ''}"
           f"--repeat {os.environ.get('REPEAT','1')} "
           f"> /root/fetchs_{os.path.basename(pair_dir)}_{role}.log 2>&1'")
    r = sh(node, cmd)
    if r.returncode:
        raise RuntimeError(f"{node} {role}: {r.stderr[:300]}")


def wait_event(node, log, timeout=600):
    """Wait until the destination's pull reported its event, or the process died."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = sh(node, f"docker exec zjn-fluid-live-r01-{node[4:]} bash -lc "
                     f"'grep -hoE \"\\\"event\\\": \\\"pull\\\"[^}}]*\" {log} 2>/dev/null | tail -1; "
                     f"grep -c Traceback {log} 2>/dev/null || true'")
        out = (r.stdout or "").strip().splitlines()
        if out and '"event": "pull"' in out[0]:
            # The grep pattern can drag a trailing fragment of interleaved NCCL output along with
            # the JSON object, which makes json.loads raise "Extra data". Parse up to the object's
            # closing brace instead of trusting the whole line.
            line = out[0]
            end = line.rfind("}")
            if end != -1:
                try:
                    return json.loads(line[:end + 1])
                except json.JSONDecodeError:
                    pass
        if len(out) > 1 and out[-1].strip() not in ("", "0"):
            raise RuntimeError(f"pair failed: {log}")
        time.sleep(1)
    raise TimeoutError(f"{log} never reported")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--dst", default="node27")
    ap.add_argument("--dst-card", default="4")
    ap.add_argument("--sources", default="node28=0,node29=1")
    ap.add_argument("--tag", default="1")
    ap.add_argument("--timeout", type=float, default=600)
    a = ap.parse_args()

    sources = [x.split("=") for x in a.sources.split(",")]
    store = "/root/chunkstore"
    have = local_chunks(a.dst, store)
    print(json.dumps({"event": "start", "dst": a.dst, "local_chunks": len(have)}), flush=True)

    rows, moved_mib, t_all = [], 0.0, time.time()
    for idx, (src, card) in enumerate(sources):
        plan_file = os.path.join(a.work, f"fetch_{src}.json")
        if not os.path.exists(plan_file):
            rows.append({"src": src, "skipped": "no plan"})
            continue
        pending = [c for c in json.load(open(plan_file)) if c not in have]
        if not pending:
            rows.append({"src": src, "chunks": 0, "note": "nothing to move"})
            continue
        pair_dir = os.path.join(a.work, f"serial_{idx}")
        os.makedirs(pair_dir, exist_ok=True)
        list_file = os.path.join(pair_dir, "chunks.json")
        with open(list_file, "w") as fh:
            json.dump(pending, fh)

        master = f"{IP[src]}:{48000 + int(a.tag) * 100 + idx * 10}"
        ack = 49000 + int(a.tag) * 100 + idx * 10
        log = f"/root/fetchs_serial_{idx}_pull.log"

        launch(a.dst, "pull", a.dst_card, list_file, master, ack, IP[src], pair_dir)
        time.sleep(2)
        launch(src, "push", card, list_file, master, ack, IP[src], pair_dir)
        try:
            ev = json.loads(wait_event(a.dst, log, a.timeout))
            rows.append({"src": src, **ev})
            moved_mib += ev.get("MiB", 0)
            have |= set(pending)
        except (RuntimeError, TimeoutError) as e:
            rows.append({"src": src, "error": str(e)[:200]})

    wall = time.time() - t_all
    print(json.dumps({"event": "fetch_serial_done", "dest": a.dst,
                      "wall_s": round(wall, 2), "MiB": round(moved_mib, 1),
                      "aggregate_GBps": round(moved_mib / 1024 / wall, 2) if wall else None,
                      "per_source": rows}, indent=1), flush=True)

    final = local_chunks(a.dst, store)
    print(json.dumps({"event": "local_after", "chunks": len(final)}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
