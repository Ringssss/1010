"""Per-node memory headroom for the keepalive experiments.

Tells us how much room each node has for pinned weight copies before the flow experiments
start -- pinning is what makes a snapshot survive, so it is the binding constraint on how many
models can coexist.

  python3 meminfo.py
"""
import sys

sys.path.insert(0, "/home/zhujianian/zhujianian/fluidckpt_zjn/codex_coding/src/task2/cluster")
import keepctl  # noqa: E402

KEYS = ("MemTotal", "MemFree", "MemAvailable", "Mlocked", "Shmem", "Cached")


def main():
    for node in ("node27", "node28", "node29"):
        ctr = keepctl.CONTAINER[node]
        keys = "|".join(KEYS)
        cmd = (f"docker exec {ctr} awk '/^({keys}):/{{printf \"%s %.1f GiB\\n\", $1, $2/1048576}}' "
               f"/proc/meminfo")
        try:
            out = keepctl._ssh(node, cmd, timeout=30)
        except Exception as e:
            print(f"{node}: {e}")
            continue
        print(f"--- {node}")
        print(out.rstrip())


if __name__ == "__main__":
    main()
