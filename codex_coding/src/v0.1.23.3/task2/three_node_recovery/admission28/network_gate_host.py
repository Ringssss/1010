"""Read one rendezvous listener and the exact data IP/interface on node28 or node30."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import resource
import signal
import subprocess
import sys

EXPECTED = {
    "node28": ("m03r2n08", "5313e6f8-1712-45dc-9855-4c47358b6c4b", "12.11.3.28", "12.11.3.30"),
    "node30": ("m03r2n10", "d9d6a6fb-013f-49ac-98f9-e77eca67952e", "12.11.3.30", "12.11.3.28"),
}


def command(argv):
    done = subprocess.run(argv, capture_output=True, timeout=3)
    return {"argv": argv, "returncode": done.returncode,
            "stdout": done.stdout.decode(errors="replace"), "stderr": done.stderr.decode(errors="replace")}


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError("network metadata 12s deadline")))
    signal.alarm(12)
    hostname, boot, local_ip, peer_ip = EXPECTED[sys.argv[1]]
    assert os.uname().nodename == hostname
    assert Path("/proc/sys/kernel/random/boot_id").read_text().strip() == boot
    address = command(["ip", "-j", "-4", "address", "show", "to", local_ip + "/32"])
    route = command(["ip", "-j", "route", "get", peer_ip])
    listeners = command(["ss", "-H", "-ltn", "sport = :29870"])
    interfaces = []
    if address["returncode"] == 0:
        interfaces = [r["ifname"] for r in json.loads(address["stdout"])
                      if any(a.get("local") == local_ip for a in r.get("addr_info", []))]
    routes = json.loads(route["stdout"]) if route["returncode"] == 0 else []
    interface = interfaces[0] if len(interfaces) == 1 else None
    checks = {"exact_address_one_interface": interface is not None,
              "peer_route_same_interface_and_source": bool(interface and len(routes) == 1
                    and routes[0].get("dev") == interface and routes[0].get("prefsrc") == local_ip),
              "rendezvous_port29870_no_listener": listeners["returncode"] == 0 and not listeners["stdout"].strip()}
    print(json.dumps({"sampled_utc": datetime.now(timezone.utc).isoformat(), "node": sys.argv[1],
                      "hostname": hostname, "boot_id": boot, "local_ip": local_ip, "peer_ip": peer_ip,
                      "socket_interface": interface, "checks": checks, "passed": all(checks.values()),
                      "commands": [address, route, listeners], "gpu_operations": 0,
                      "port_checks": [29870], "scope": "one local listener query; no socket connect or port scan"}, indent=2))


if __name__ == "__main__":
    main()
