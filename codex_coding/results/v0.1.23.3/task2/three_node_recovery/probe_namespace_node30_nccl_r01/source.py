"""CPU-only namespace sysfs evidence for the existing physical XML validator."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import signal
import socket

from init_gate import RESULT_ROOT, boot_id, write


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError("namespace read deadline")))
    signal.alarm(8)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out = args.out.resolve()
    if args.out.parent != RESULT_ROOT or not args.out.name.startswith("probe_namespace_"):
        parser.error("require new probe_namespace_* directory")
    host = json.loads(args.host.read_text())
    if socket.gethostname() != host["hostname"] or boot_id() != host["boot_id"]:
        parser.error("host/container namespace hostname or boot differs from host evidence")
    args.out.mkdir(parents=True, exist_ok=False)
    devices = []
    paths = {entry["sysfs"] for row in host["kfd"] + host["hca"] for entry in row["pci_chain"]}
    for name in sorted(paths):
        path = Path(name).resolve(strict=True)
        if not path.is_relative_to("/sys/devices"):
            raise ValueError("unexpected sysfs physical path")
        devices.append({"resolved": str(path), "numa_node": (path / "numa_node").read_text().strip(),
                        "local_cpulist": (path / "local_cpulist").read_text().strip()})
    evidence = {"hostname": socket.gethostname(), "boot_id": boot_id(),
                "pid_namespace": os.readlink("/proc/self/ns/pid"), "namespace_sysfs": {"devices": devices},
                "scope": "container namespace PCI paths; Docker full identity requires external host inspect",
                "host_source": {"path": str(args.host.resolve()), "sha256": hashlib.sha256(args.host.read_bytes()).hexdigest()},
                "gpu_operations": 0}
    write(args.out / "container.json", evidence)
    (args.out / "source.py").write_bytes(Path(__file__).read_bytes())
    print(json.dumps({"out": str(args.out), "devices": len(devices), "gpu_operations": 0}))


if __name__ == "__main__":
    main()
