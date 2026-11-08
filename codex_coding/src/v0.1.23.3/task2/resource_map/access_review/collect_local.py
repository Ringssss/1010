#!/usr/bin/env python3
"""Collect local SSH ownership and existing control-path metadata without connecting."""
import argparse
import base64
import collections
import datetime
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import subprocess

TARGETS = {"10.211.3.29", "11.211.3.29", "12.11.3.29"}
HOSTS = ["node29", *sorted(TARGETS)]
SAFE_KEYS = {"hostname", "user", "port", "controlmaster", "controlpath", "controlpersist",
             "proxyjump", "hostkeyalias", "userknownhostsfile", "globalknownhostsfile",
             "stricthostkeychecking", "updatehostkeys"}


def run(argv):
    p = subprocess.run(argv, capture_output=True, text=True, timeout=5)
    return {"argv": argv, "returncode": p.returncode, "stdout": p.stdout, "stderr": p.stderr}


def digest(path):
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    uid = os.getuid()
    home = Path.home()
    result = {"collected_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "hostname": socket.gethostname(), "uid": uid,
              "actions": {"new_network_connections": 0, "mux_channels": 0, "signals": 0,
                          "gpu_calls": 0, "system_configuration_writes": 0},
              "visibility": ["Current network/PID namespace only; no remote sshd state.",
                             "Process details restricted to current UID; command payloads omitted.",
                             "TCP table can include other users; ownership only resolved for current UID.",
                             "Existing Unix socket metadata does not prove an authenticated SSH master."]}
    known_paths = [home / ".ssh/known_hosts", home / ".ssh/known_hosts2",
                   Path("/etc/ssh/ssh_known_hosts"), Path("/etc/ssh/ssh_known_hosts2")]
    result["known_hosts_sha_before"] = {str(p): digest(p) for p in known_paths}
    settings = {}
    config_errors = []
    for host in HOSTS:
        r = run(["ssh", "-G", "-o", "StrictHostKeyChecking=yes", "-o", "UpdateHostKeys=no", host])
        safe = {}
        for line in r["stdout"].splitlines():
            key, _, value = line.partition(" ")
            if key in SAFE_KEYS:
                safe[key] = value
            if key == "proxycommand":
                safe[key] = "none" if value == "none" else "present; command omitted"
        settings[host] = safe
        if r["returncode"]:
            config_errors.append({"host": host, "returncode": r["returncode"]})
    result["effective_settings"] = settings
    result["config_errors"] = config_errors
    known = []
    for host in HOSTS:
        for path in known_paths:
            if not path.is_file():
                continue
            r = run(["ssh-keygen", "-F", host, "-f", str(path)])
            entries = []
            for line in r["stdout"].splitlines():
                if line.startswith("#"):
                    continue
                fields = line.split()
                offset = 1 if fields and fields[0].startswith("@") else 0
                if len(fields) >= offset + 3:
                    key_type, key = fields[offset + 1:offset + 3]
                    raw = base64.b64decode(key)
                    entries.append({"key_type": key_type, "fingerprint": "SHA256:" +
                                    base64.b64encode(hashlib.sha256(raw).digest()).decode().rstrip("=")})
            known.append({"host": host, "file": str(path), "returncode": r["returncode"], "entries": entries})
    result["known_hosts_public_fingerprints"] = known
    own_ssh = []
    own_inode_pids = collections.defaultdict(list)
    own_proc_errors = collections.Counter()
    d_processes = []
    process_state_visibility = collections.Counter()
    socket_candidates = set()
    for process in Path("/proc").iterdir():
        if not process.name.isdigit():
            continue
        try:
            process_uid = process.stat().st_uid
            status_fields = (process / "stat").read_text().rsplit(")", 1)[1].split()
            process_state_visibility["readable_processes"] += 1
            if status_fields[0] == "D":
                d_processes.append({"pid": int(process.name), "uid": process_uid,
                                    "comm": (process / "comm").read_text().strip(),
                                    "current_uid": process_uid == uid})
            if process_uid != uid:
                continue
            comm = (process / "comm").read_text().strip()
            fd_dir = process / "fd"
            for fd in fd_dir.iterdir():
                try:
                    link = os.readlink(fd)
                    if link.startswith("socket:["):
                        own_inode_pids[link[8:-1]].append({"pid": int(process.name), "comm": comm})
                except OSError:
                    own_proc_errors["fd_read_error"] += 1
            if comm not in {"ssh", "sshd", "ssh-agent"}:
                continue
            fields = (process / "stat").read_text().rsplit(")", 1)[1].split()
            argv = (process / "cmdline").read_bytes().split(b"\0")
            tokens = [a.decode(errors="replace") for a in argv if a]
            control_paths = []
            for i, token in enumerate(tokens):
                if token == "-S" and i + 1 < len(tokens):
                    control_paths.append(tokens[i + 1])
                elif token.startswith("ControlPath="):
                    control_paths.append(token.split("=", 1)[1])
            socket_candidates.update(control_paths)
            own_ssh.append({"pid": int(process.name), "ppid": int(fields[1]), "comm": comm,
                            "state": fields[0], "start_ticks": int(fields[19]),
                            "known_target_tokens": [t for t in tokens if t in set(HOSTS) or
                                                    any(t.endswith("@" + h) for h in HOSTS)],
                            "explicit_control_paths": control_paths,
                            "argv_payload_omitted": True})
        except OSError:
            own_proc_errors["process_unavailable"] += 1
    result["current_uid_ssh_processes"] = own_ssh
    result["proc_visibility_errors"] = dict(own_proc_errors)
    result["existing_d_state_processes"] = d_processes
    result["process_state_visibility"] = dict(process_state_visibility)
    mountstats_blocks = []
    current_block = []
    for line in Path("/proc/self/mountstats").read_text().splitlines():
        if line.startswith("device "):
            if current_block and any(target in current_block[0] for target in ["node29-ib0:", "12.11.3.29:"]):
                mountstats_blocks.append(current_block)
            current_block = [line]
        elif current_block:
            current_block.append(line)
    if current_block and any(target in current_block[0] for target in ["node29-ib0:", "12.11.3.29:"]):
        mountstats_blocks.append(current_block)
    result["node29_nfs_mountstats_passive"] = mountstats_blocks
    tcp = []
    state_names = {"01": "ESTABLISHED", "02": "SYN_SENT", "03": "SYN_RECV", "04": "FIN_WAIT1",
                   "05": "FIN_WAIT2", "06": "TIME_WAIT", "07": "CLOSE", "08": "CLOSE_WAIT",
                   "09": "LAST_ACK", "0A": "LISTEN", "0B": "CLOSING"}
    for path in [Path("/proc/net/tcp"), Path("/proc/net/tcp6")]:
        for line in path.read_text().splitlines()[1:]:
            fields = line.split()
            remote_hex, port_hex = fields[2].split(":")
            local_port = int(fields[1].split(":")[1], 16)
            if int(port_hex, 16) != 22 and local_port != 22:
                continue
            raw = bytes.fromhex(remote_hex)
            if path.name == "tcp":
                remote = socket.inet_ntop(socket.AF_INET, raw[::-1])
            else:
                remote = socket.inet_ntop(socket.AF_INET6, b"".join(raw[i:i + 4][::-1] for i in range(0, 16, 4)))
                if remote.startswith("::ffff:"):
                    remote = remote[7:]
            if remote not in TARGETS:
                continue
            tcp.append({"table": path.name, "remote_ip": remote, "remote_port": int(port_hex, 16),
                        "local_port": local_port,
                        "local_hex_endpoint": fields[1], "state": state_names.get(fields[3], fields[3]),
                        "uid": int(fields[7]), "inode": fields[9],
                        "current_uid_owners": own_inode_pids.get(fields[9], [])})
    result["node29_tcp22_connections"] = tcp
    result["connection_state_counts"] = dict(collections.Counter(x["state"] for x in tcp))
    result["own_connection_count"] = sum(x["uid"] == uid for x in tcp)
    for host_settings in settings.values():
        candidate = host_settings.get("controlpath")
        if candidate and "%" not in candidate and candidate != "none":
            socket_candidates.add(candidate)
    unix_rows = []
    for line in Path("/proc/net/unix").read_text().splitlines()[1:]:
        fields = line.split(maxsplit=7)
        if len(fields) < 8 or fields[7].startswith("@"):
            continue
        path = Path(fields[7])
        # Restrict metadata lookup to local socket roots, avoiding NFS traversal.
        local_roots = [home / ".ssh", Path("/tmp"), Path("/run")]
        if not any(path.is_relative_to(root) for root in local_roots):
            continue
        owners = own_inode_pids.get(fields[6], [])
        relevant = str(path) in socket_candidates or any(p["comm"] in {"ssh", "ssh-agent"} for p in owners)
        if not relevant and not path.is_relative_to(home / ".ssh"):
            continue
        try:
            meta = path.stat()
            if meta.st_uid != uid or not stat.S_ISSOCK(meta.st_mode):
                continue
            unix_rows.append({"path": str(path), "uid": meta.st_uid, "mode": oct(stat.S_IMODE(meta.st_mode)),
                              "inode": fields[6], "current_uid_owners": owners,
                              "configured_control_path": str(path) in socket_candidates,
                              "authenticated_master_verified": False})
        except OSError:
            continue
    result["existing_owned_ssh_socket_candidates"] = unix_rows
    result["known_hosts_sha_after"] = {str(p): digest(p) for p in known_paths}
    result["known_hosts_unchanged"] = result["known_hosts_sha_before"] == result["known_hosts_sha_after"]
    (args.output / "snapshot.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    manifest = {"source": str(Path(__file__).resolve()), "source_sha256": digest(Path(__file__)),
                "snapshot_sha256": digest(args.output / "snapshot.json")}
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"result_dir": str(args.output), "own_ssh_processes": len(own_ssh),
                      "tcp22_connection_count": len(tcp), "own_connection_count": result["own_connection_count"],
                      "owned_socket_candidates": len(unix_rows), "known_hosts_unchanged": result["known_hosts_unchanged"]}))


if __name__ == "__main__":
    main()
