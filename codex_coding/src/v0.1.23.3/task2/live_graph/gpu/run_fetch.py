"""Pull a model's chunks into one node from every other holder, in parallel.

Each source feeds the destination over its own process pair and its own rendezvous/ack ports.
Pairs are separate processes because one RCCL process per container is what works on this fabric.

The destination reuses whatever chunks it already has, so this measures a genuine partial-replica
restore: node27 holds some blocks locally and pulls the rest from three peers at once.

Usage (from any node):
  python3 run_fetch.py --work <dir> --dst node27 --dst-card 4 [--src-cards node28=0,node29=1,node30=2]
"""
import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

REPO = "/home/zhujianian/zhujianian/fluidckpt_zjn"
SCRIPT = f"{REPO}/codex_coding/src/v0.1.23.3/task2/live_graph/gpu/chunk_fluid.py"
IP = {"node27": "12.11.3.27", "node28": "12.11.3.28",
      "node29": "12.11.3.29", "node30": "12.11.3.30"}


def sh(node, cmd, timeout=1800):
    return subprocess.run(["ssh", "-n", "-o", "BatchMode=yes", node, cmd],
                          capture_output=True, text=True, timeout=timeout)


def launch(node, role, card, chunks_path, master, ack, peer_ip, tag, idx, pair_dir):
    # A single visible card hangs the first collective on this stack, so expose the four-card
    # window and address the target as an ordinal inside it. `card` is the physical index the
    # caller wants; `slot` lets two pairs on the same node land on different cards.
    c = int(card)
    base = (c // 4) * 4
    slot = c % 4
    vis = ",".join(str(base + i) for i in range(4))
    cmd = (f"docker exec -d zjn-fluid-live-r01-{node[4:]} bash -lc '"
           f"cd {REPO}; export PYTHONPATH=main_lib HIP_VISIBLE_DEVICES={vis}; "
           f"LD_LIBRARY_PATH=/opt/hyhal/lib:$LD_LIBRARY_PATH; "
           f"export NCCL_NET_PLUGIN=librccl-net-shca.so NCCL_SOCKET_IFNAME=ib0 NCCL_DEBUG=WARN; "
           f"ulimit -c 0; "
           f"python3 {SCRIPT} {role} --store /root/chunkstore --chunks {chunks_path} "
           f"--out {pair_dir} --master {master} --ack-port {ack} --peer-ip {peer_ip} --gpu {slot} "
           f"> /root/fetch_{tag}_{idx}_{role}.log 2>&1'")
    r = sh(node, cmd)
    if r.returncode:
        raise RuntimeError(f"{node} {role}: {r.stderr[:300]}")


def wait_done(dst, tag, idx, timeout=600):
    """The destination prints one 'pull' event when its pair finishes."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = sh(dst, f"docker exec zjn-fluid-live-r01-{dst[4:]} bash -lc "
                    f"'grep -c \"\\\"event\\\": \\\"pull\\\"\" /root/fetch_{tag}_{idx}_pull.log 2>/dev/null || true'")
        try:
            if int((r.stdout or "0").strip().split()[0]) >= 1:
                return True
        except (ValueError, IndexError):
            pass
        time.sleep(2)
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--dst", default="node27")
    ap.add_argument("--dst-card", default="4")
    ap.add_argument("--src-cards", default="node28=0,node29=1,node30=2")
    ap.add_argument("--tag", default="1")
    ap.add_argument("--keep", default="", help="comma list of chunk ids the destination already has")
    a = ap.parse_args()

    src_cards = dict(x.split("=") for x in a.src_cards.split(","))
    # Restrict each source's list to chunks the destination does not already hold.
    have = set(filter(None, a.keep.split(",")))
    pairs = []
    for src, card in src_cards.items():
        p = os.path.join(a.work, f"fetch_{src}.json")
        if not os.path.exists(p):
            continue
        ids = [c for c in json.load(open(p)) if c not in have]
        if not ids:
            continue
        dst_list = os.path.join(a.work, f"to_pull_{src}.json")
        with open(dst_list, "w") as fh:
            json.dump(ids, fh)
        pairs.append((src, card, dst_list, len(ids)))

    if not pairs:
        print(json.dumps({"event": "nothing_to_fetch"}))
        return 0

    for idx, _ in enumerate(pairs):
        os.makedirs(os.path.join(a.work, f"pair_{idx}"), exist_ok=True)
    t0 = time.time()
    for idx, (src, card, path, _n) in enumerate(pairs):
        # rank 0 is the PUSH side, so the TCP store lives on the SOURCE node. Pointing it at the
        # destination leaves both ends waiting for a store nobody serves -- the same mistake as the
        # earlier single-pair launcher, and it fails silently with empty logs.
        master = f"{IP[src]}:{46000 + int(a.tag) * 100 + idx * 10}"
        ack = 47000 + int(a.tag) * 100 + idx * 10
        # One destination CARD per pair. All three on the same card stacks three communicators on
        # one device, which measured ~15 MB/s instead of ~45; separate cards keep each channel at
        # line rate and are what the 4x1.4 GiB / 178 GB/s run did.
        dst_card = str(int(a.dst_card) + idx)
        launch(a.dst, "pull", dst_card, path, master, ack, IP[src], a.tag, idx,
               f"{a.work}/pair_{idx}")
    time.sleep(3)
    for idx, (src, card, path, _n) in enumerate(pairs):
        # rank 0 is the PUSH side, so the TCP store lives on the SOURCE node. Pointing it at the
        # destination leaves both ends waiting for a store nobody serves -- the same mistake as the
        # earlier single-pair launcher, and it fails silently with empty logs.
        master = f"{IP[src]}:{46000 + int(a.tag) * 100 + idx * 10}"
        ack = 47000 + int(a.tag) * 100 + idx * 10
        launch(src, "push", card, path, master, ack, IP[src], a.tag, idx,
               f"{a.work}/pair_{idx}")

    with ThreadPoolExecutor(len(pairs)) as ex:
        done = list(ex.map(lambda t: wait_done(a.dst, a.tag, t[0]),
                           [(i, p) for i, p in enumerate(pairs)]))
    wall = time.time() - t0

    results = []
    for idx, (src, card, path, n) in enumerate(pairs):
        r = sh(a.dst, f"docker exec zjn-fluid-live-r01-{a.dst[4:]} bash -lc "
                      f"'grep -h \"\\\"event\\\": \\\"pull\\\"\" /root/fetch_{a.tag}_{idx}_pull.log 2>/dev/null | tail -1'")
        line = (r.stdout or "").strip().splitlines()
        try:
            results.append({"src": src, "chunks": n, **json.loads(line[-1])})
        except (json.JSONDecodeError, IndexError):
            results.append({"src": src, "chunks": n, "error": (r.stderr or "")[:200]})
    total_mib = sum(x.get("MiB", 0) for x in results)
    print(json.dumps({"event": "fetch_done", "wall_s": round(wall, 2),
                      "sources": len(pairs), "MiB": round(total_mib, 1),
                      "aggregate_GBps": round(total_mib / 1024 / wall, 2) if wall else None,
                      "per_source": results}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
