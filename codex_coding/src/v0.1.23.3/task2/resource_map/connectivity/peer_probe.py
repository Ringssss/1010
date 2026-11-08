"""Run a fixed read-only cross-source observation inside node28."""
from datetime import datetime, timezone
from pathlib import Path
import json
import socket
import subprocess
import time


def now():
    return datetime.now(timezone.utc).isoformat()


def cmd(argv, timeout):
    begin = time.monotonic()
    row = dict(argv=argv, started_at=now(), timeout_s=timeout)
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        row.update(returncode=p.returncode, stdout=p.stdout, stderr=p.stderr)
    except subprocess.TimeoutExpired as exc:
        def decode(value):
            return value.decode(errors="replace") if isinstance(value, bytes) else value or ""
        row.update(returncode=None, timed_out=True, stdout=decode(exc.stdout), stderr=decode(exc.stderr))
    row.update(elapsed_s=time.monotonic() - begin, finished_at=now())
    return row


identity = dict(hostname=socket.gethostname(), boot_id=Path("/proc/sys/kernel/random/boot_id").read_text().strip())
if identity != {"hostname": "m03r2n08", "boot_id": "5313e6f8-1712-45dc-9855-4c47358b6c4b"}:
    raise ValueError("Node28 source identity changed")
result = dict(source_node="node28", identity=identity, started_at=now(), signals=0, gpu_operations=0, observations=[])
for ip in ("10.211.3.29", "12.11.3.29"):
    for kind in ("icmp", "tcp22_banner"):
        row = dict(kind=kind, target=ip, source_node="node28", resolved_address=ip, started_at=now(),
                   route=cmd(["ip", "-j", "route", "get", ip], 2))
        if kind == "icmp":
            row.update(cmd(["ping", "-n", "-c", "1", "-W", "2", ip], 3))
            row["status"] = "reply" if row["returncode"] == 0 else "no_reply"
        else:
            begin = time.monotonic()
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            row.update(timeout_s=3, port=22, stdout="", stderr="", connect_ok=False, banner_ok=False)
            try:
                sock.settimeout(3)
                sock.connect((ip, 22))
                row.update(connect_ok=True, connected_after_s=time.monotonic() - begin,
                           actual_local_address=list(sock.getsockname()), actual_peer_address=list(sock.getpeername()))
                sock.settimeout(max(0.001, 3 - (time.monotonic() - begin)))
                banner = sock.recv(512)
                row.update(stdout=banner.decode(errors="replace"), banner_ok=banner.startswith(b"SSH-"),
                           status="ssh_banner" if banner.startswith(b"SSH-") else "closed_or_nonssh_banner")
            except socket.timeout as exc:
                row.update(status="banner_timeout" if row["connect_ok"] else "connect_timeout", stderr=repr(exc))
            except OSError as exc:
                row.update(status="banner_error" if row["connect_ok"] else "connect_error", stderr=repr(exc))
            finally:
                sock.close()
            row.update(elapsed_s=time.monotonic() - begin, finished_at=now())
        result["observations"].append(row)
for port in (33000, 33001):
    ip = "12.11.3.29"
    row = dict(kind="http_inventory", source_node="node28", target=ip, port=port,
               route=cmd(["ip", "-j", "route", "get", ip], 2))
    row.update(cmd(["curl", "--noproxy", "*", "--connect-timeout", "4", "--max-time", "4", "-sS", "-D", "-",
                    "-w", "\nhttp_status=%{http_code}\nlocal_ip=%{local_ip}\nremote_ip=%{remote_ip}\n",
                    f"http://{ip}:{port}/inventory?compact=1"], 5))
    row["status"] = "http_ok" if row["returncode"] == 0 and "http_status=200" in row["stdout"] else "http_failed"
    result["observations"].append(row)
result.update(finished_at=now(), neighbors=cmd(["ip", "-j", "neigh", "show"], 2))
print(json.dumps(result))
