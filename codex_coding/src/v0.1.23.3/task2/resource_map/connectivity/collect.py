"""Collect one bounded read-only network observation per registered endpoint."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import time

ROOT = Path(__file__).resolve().parents[6]
BASE = ROOT / "codex_coding/results/v0.1.23.3/task2/resource_map"
REMOTE = "hostname; cat /proc/sys/kernel/random/boot_id; cat /proc/uptime"


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def command(argv, timeout):
    begin = time.monotonic()
    result = {"argv": argv, "started_at": now(), "timeout_s": timeout}
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        result.update(returncode=done.returncode, stdout=done.stdout, stderr=done.stderr)
    except subprocess.TimeoutExpired as exc:
        def decode(value):
            return value.decode(errors="replace") if isinstance(value, bytes) else value or ""
        result.update(returncode=None, timed_out=True, stdout=decode(exc.stdout), stderr=decode(exc.stderr))
    result.update(finished_at=now(), elapsed_s=time.monotonic() - begin)
    return result


def route(ip):
    return command(["ip", "-j", "route", "get", ip], 2)


def ssh_args(node, ip):
    return ["ssh", "-T", "-n", "-v", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
            "-o", "ConnectionAttempts=1", "-o", "StrictHostKeyChecking=yes", "-o", "UpdateHostKeys=no",
            "-o", "ServerAliveInterval=2", "-o", "ServerAliveCountMax=1", "-o", "Hostname=" + ip,
            node, REMOTE]


def ssh_status(row):
    stderr = row["stderr"].lower()
    if row["returncode"] == 0:
        return "remote_exec_ok"
    if "host key verification failed" in stderr or "remote host identification has changed" in stderr:
        return "hostkey_rejected"
    if "permission denied" in stderr or "no supported authentication methods" in stderr:
        return "auth_failed"
    if "connection timed out during banner exchange" in stderr:
        return "banner_timeout"
    if "connect to host" in stderr and "connection timed out" in stderr:
        return "connect_timeout"
    if "authenticated to" in stderr:
        return "remote_exec_timeout" if row.get("timed_out") else "remote_exec_failed"
    if "connection established" in stderr:
        return "handshake_timeout" if row.get("timed_out") else "handshake_closed_or_failed"
    return "connect_or_config_failed"


def probe(kind, node, ip, out, port=None):
    row = {"kind": kind, "node": node, "target": ip, "started_at": now(), "resolved_address": ip,
           "route": route(ip), "source_node": "node27"}
    if kind == "icmp":
        row.update(command(["ping", "-n", "-c", "1", "-W", "2", ip], 3))
        row["status"] = "reply" if row["returncode"] == 0 else "no_reply"
    elif kind == "ssh":
        row.update(command(ssh_args(node, ip), 8))
        row["status"] = ssh_status(row)
    elif kind == "tcp22_banner":
        begin = time.monotonic()
        row.update(timeout_s=3, port=22, stdout="", stderr="", connect_ok=False, banner_ok=False)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
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
    elif kind == "http_inventory":
        row.update(port=port, url=f"http://{ip}:{port}/inventory?compact=1")
        body = out / f"{node}_{port}.inventory.raw"
        argv = ["curl", "--noproxy", "*", "--connect-timeout", "4", "--max-time", "4", "-sS",
                "-D", "-", "-o", str(body), "-w", "\nhttp_status=%{http_code}\nlocal_ip=%{local_ip}\nremote_ip=%{remote_ip}\n", row["url"]]
        row.update(command(argv, 5))
        row["status"] = "http_ok" if row["returncode"] == 0 and "http_status=200" in row["stdout"] else "http_failed"
        if body.exists():
            row.update(body_file=body.name, body_sha256=sha(body), body_bytes=body.stat().st_size)
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    if out.parent != BASE or not out.name.startswith("connectivity_"):
        parser.error("require a new resource_map/connectivity_* directory")
    out.mkdir(parents=True, exist_ok=False)
    (out / "source.py").write_bytes(Path(__file__).read_bytes())
    known_paths = [Path.home() / ".ssh/known_hosts", Path.home() / ".ssh/known_hosts2"]
    known_before = {str(p): sha(p) if p.exists() else None for p in known_paths}
    targets = [{"node": f"node{n}", "ip": f"{prefix}.{n}", "plane": plane}
               for n in (27, 28, 29, 30) for prefix, plane in (("10.211.3", "management"), ("12.11.3", "data"))]
    summary = {"started_at": now(), "source_host": socket.gethostname(), "source_boot_id":
               Path("/proc/sys/kernel/random/boot_id").read_text().strip(), "signals": 0, "gpu_operations": 0,
               "targets": targets, "observations": [], "known_hosts_before": known_before,
               "source_metadata": command(["ip", "-j", "address", "show"], 2), "ssh_configs": []}
    for target in targets:
        cfg = command(["ssh", "-G", "-o", "Hostname=" + target["ip"], target["node"]], 2)
        settings = dict(line.split(" ", 1) for line in cfg["stdout"].splitlines() if " " in line)
        summary["ssh_configs"].append({"node": target["node"], "ip": target["ip"], "effective":
                                      {k: settings.get(k) for k in ("hostname", "user", "port", "proxycommand", "proxyjump", "hostkeyalias")}})
        if settings.get("hostname") != target["ip"] or settings.get("port") != "22" or any(
                settings.get(k) not in (None, "none") for k in ("proxycommand", "proxyjump")):
            raise ValueError("Unexpected SSH destination/proxy configuration")
    save(out / "summary.json", summary)
    def node_round(node):
        rows = []
        for target in targets:
            if target["node"] == node:
                for kind in ("icmp", "tcp22_banner", "ssh"):
                    rows.append(probe(kind, node, target["ip"], out))
        return rows

    def record(row):
        name = f"{row['kind']}_{row['target'].replace('.', '_')}" + (f"_{row['port']}" if row.get("port") else "")
        save(out / (name + ".json"), row)
        summary["observations"].append(row)
        save(out / "summary.json", summary)

    with ThreadPoolExecutor(max_workers=4) as pool:
        # Keep all addresses of a host sequential to avoid MaxStartups interference.
        jobs = [pool.submit(node_round, f"node{n}") for n in (27, 28, 29, 30)]
        for future in as_completed(jobs):
            for row in future.result():
                record(row)
        jobs = [pool.submit(probe, "http_inventory", f"node{n}", f"12.11.3.{n}", out, port)
                for n in (27, 29) for port in (33000, 33001)]
        for future in as_completed(jobs):
            record(future.result())
    summary.update(finished_at=now(), status="read_only_complete", known_hosts_after=
                   {str(p): sha(p) if p.exists() else None for p in known_paths})
    summary["known_hosts_unchanged"] = summary["known_hosts_before"] == summary["known_hosts_after"]
    summary["observations"].sort(key=lambda r: (r["node"], r["target"], r["kind"], r.get("port", 0)))
    save(out / "summary.json", summary)
    lines = ["# 节点连通性单轮只读采样", "", f"来源：node27 / {summary['source_host']}；UTC {summary['started_at']}。", "",
             "| 节点 | 地址 | ICMP | TCP22/banner | SSH只读身份 | 实际路由 |", "|---|---|---|---|---|---|"]
    for target in targets:
        rows = {r["kind"]: r for r in summary["observations"] if r["target"] == target["ip"]}
        actual_route = rows["icmp"]["route"]["stdout"].strip().replace("\n", " ")
        lines.append(f"| {target['node']} | {target['ip']} | {rows['icmp']['status']} | {rows['tcp22_banner']['status']} | {rows['ssh']['status']} | `{actual_route}` |")
    lines += ["", "| Keeper | HTTP inventory | 耗时(s) |", "|---|---|---|"]
    for row in summary["observations"]:
        if row["kind"] == "http_inventory":
            lines.append(f"| {row['node']}:{row['port']} | {row['status']} | {row['elapsed_s']:.3f} |")
    lines += ["", "每目标ICMP一包/2s，TCP+banner共3s，SSH连接5s/外层8s，keeper inventory总4s；每项一次。",
              "SSH严格hostkey且禁用UpdateHostKeys；known_hosts前后SHA保持：" + str(summary["known_hosts_unchanged"]) + "。",
              "节点28/30连通不代表GPU资源可使用；ICMP或TCP握手成功不证明远端用户态/容器/keeper可执行。",
              "该采样没有建立HIP初始化故障的因果，也没有证明node29旧探针退出或保护对象完整。"]
    (out / "report.md").write_text("\n".join(lines) + "\n")
    save(out / "manifest.json", {"source_sha256": sha(Path(__file__)), "files":
         {p.name: sha(p) for p in sorted(out.iterdir()) if p.is_file()}})
    print(json.dumps({"out": str(out), "known_hosts_unchanged": summary["known_hosts_unchanged"],
                      "observations": len(summary["observations"])}))


if __name__ == "__main__":
    main()
