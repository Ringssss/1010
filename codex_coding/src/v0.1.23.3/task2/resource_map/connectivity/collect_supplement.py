"""Collect only an authorized node28 cross-check and one known alternate address."""
import hashlib
import json
from pathlib import Path
import subprocess
import time

from collect import BASE, command, now, probe, save, sha


def main():
    out = BASE / "connectivity_cross_alt_r01"
    out.mkdir(exist_ok=False)
    directory = Path(__file__).resolve().parent
    for name in ("collect.py", "peer_probe.py", "collect_supplement.py"):
        (out / name).write_bytes((directory / name).read_bytes())
    known = [Path.home() / ".ssh/known_hosts", Path.home() / ".ssh/known_hosts2"]
    before = {str(p): sha(p) if p.exists() else None for p in known}
    argv = ["ssh", "-T", "-v", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-o", "ConnectionAttempts=1",
            "-o", "StrictHostKeyChecking=yes", "-o", "UpdateHostKeys=no", "-o", "ServerAliveInterval=2",
            "-o", "ServerAliveCountMax=1", "-o", "Hostname=12.11.3.28", "node28", "python3 -"]
    begin = time.monotonic()
    cross = dict(argv=argv, started_at=now(), timeout_s=30, remote_source_sha256=sha(directory / "peer_probe.py"))
    try:
        p = subprocess.run(argv, input=(directory / "peer_probe.py").read_text(), text=True, capture_output=True, timeout=30)
        cross.update(returncode=p.returncode, stdout=p.stdout, stderr=p.stderr)
    except subprocess.TimeoutExpired as exc:
        def decode(value):
            return value.decode(errors="replace") if isinstance(value, bytes) else value or ""
        cross.update(returncode=None, timed_out=True, stdout=decode(exc.stdout), stderr=decode(exc.stderr))
    cross.update(elapsed_s=time.monotonic() - begin, finished_at=now())
    save(out / "peer_ssh.json", cross)
    if cross["returncode"] == 0:
        save(out / "node28_cross.json", json.loads(cross["stdout"]))
    alternate = []
    for kind in ("icmp", "tcp22_banner", "ssh"):
        row = probe(kind, "node29", "11.211.3.29", out)
        save(out / (kind + "_11_211_3_29.json"), row)
        alternate.append(row)
    after = {str(p): sha(p) if p.exists() else None for p in known}
    save(out / "summary.json", {"cross_returncode": cross["returncode"], "alternate_observations": alternate,
         "known_hosts_before": before, "known_hosts_after": after, "known_hosts_unchanged": before == after,
         "signals": 0, "gpu_operations": 0, "finished_at": now(),
         "alternate_address_evidence": str(BASE / "connectivity_nodes_r01/local_network_snapshot.json")})
    save(out / "manifest.json", {"files": {p.name: sha(p) for p in sorted(out.iterdir()) if p.is_file()}})
    print(json.dumps({"out": str(out), "cross_returncode": cross["returncode"], "alternate":
                      [{"kind": r["kind"], "status": r["status"]} for r in alternate]}))


if __name__ == "__main__":
    main()
