"""Bounded node27 metadata-only diagnosis; no tensor, collective or engine RPC."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import ctypes
import ctypes.util
import hashlib
import json
import os
from pathlib import Path
import resource
import shlex
import signal
import subprocess
import sys
import time
import urllib.request

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]
RESULT_ROOT = ROOT / "codex_coding/results/v0.1.23.3/task2/resource_map"
CONTAINER = "zjn-fluid-basic-1bce3897a2075b"
BDFS = ("0000:09:00.0", "0000:36:00.0")


def write(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    path.chmod(0o666)


def event(out, phase, **values):
    with (out / "diagnosis.events.jsonl").open("a") as handle:
        handle.write(json.dumps({"phase": phase, "pid": os.getpid(), "monotonic_ns": time.monotonic_ns(), **values}) + "\n")
        handle.flush()


def proc(pid):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
        return {"pid": pid, "start_time": fields[19], "state": fields[0], "live": fields[0] != "Z"}
    except FileNotFoundError:
        return {"pid": pid, "live": False}


def bounded(argv, env, out, name, seconds):
    """Capture and clean only the newly created PID/starttime/argv/PGID."""
    log = out / f"{name}.log"
    with log.open("wb") as handle:
        child = subprocess.Popen(argv, env=env, stdout=handle, stderr=subprocess.STDOUT,
                                 start_new_session=True)
    expected = proc(child.pid)
    expected.update(argv=argv, pgid=child.pid)
    write(out / f"{name}.owner.json", expected)
    started = time.monotonic_ns()
    timed_out, cleanup = False, {"passed": False}
    try:
        child.wait(timeout=seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
    finally:
        actual = proc(child.pid)
        if actual["live"]:
            live_argv = [s.decode() for s in Path(f"/proc/{child.pid}/cmdline").read_bytes().split(b"\0") if s]
            if (actual.get("start_time") != expected.get("start_time") or
                    os.getpgid(child.pid) != expected["pgid"] or live_argv != argv):
                cleanup = {"passed": False, "reason": "exact new process owner mismatch; no signal"}
            else:
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    if proc(child.pid).get("start_time") != expected.get("start_time"):
                        raise RuntimeError("PID identity changed before exact KILL")
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait(timeout=1)
                cleanup = {"passed": True, "signals": ["TERM"] if child.returncode != -9 else ["TERM", "KILL"]}
        else:
            cleanup = {"passed": True, "already_exited": True}
    result = {"argv": argv, "pid": child.pid, "start_time": expected.get("start_time"),
              "returncode": child.returncode, "timed_out": timed_out, "cleanup": cleanup,
              "elapsed_ns": time.monotonic_ns()-started, "timeout_seconds": seconds,
              "actual_after": proc(child.pid), "log": str(log)}
    write(out / f"{name}.process.json", result)
    return result


def hip_child(args):
    def expired(signum, frame):
        event(args.out, f"{args.case}_alarm_expired")
        raise TimeoutError("HIP metadata alarm expired")
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(10)
    event(args.out, f"{args.case}_child_enter", core_dump_limit=resource.getrlimit(resource.RLIMIT_CORE),
          visibility={k: os.environ.get(k) for k in ("HIP_VISIBLE_DEVICES", "CUDA_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES")})
    event(args.out, f"{args.case}_library_load_enter")
    library = ctypes.CDLL(ctypes.util.find_library("amdhip64") or "libamdhip64.so")
    event(args.out, f"{args.case}_library_load_exit", library=library._name)
    event(args.out, f"{args.case}_hipInit_enter")
    rc = library.hipInit(0)
    event(args.out, f"{args.case}_hipInit_exit", returncode=rc)
    result = {"case": args.case, "hipInit_returncode": rc, "gpus": []}
    if rc == 0:
        count = ctypes.c_int()
        event(args.out, f"{args.case}_device_count_enter")
        rc_count = library.hipGetDeviceCount(ctypes.byref(count))
        event(args.out, f"{args.case}_device_count_exit", returncode=rc_count, count=count.value)
        result.update(count_returncode=rc_count, count=count.value)
        for ordinal in range(count.value):
            bdf = ctypes.create_string_buffer(32)
            rc_bdf = library.hipDeviceGetPCIBusId(bdf, len(bdf), ordinal)
            result["gpus"].append({"logical_gpu": ordinal, "returncode": rc_bdf, "bdf": bdf.value.decode()})
    write(args.out / f"{args.case}.hip.json", result)
    event(args.out, f"{args.case}_child_exit")


def trials(args):
    # Each range is attempted once. No Torch import, device allocation or NCCL.
    results = []
    for case in ("all_visible", "gpu01_visible"):
        env = dict(os.environ)
        for key in ("HIP_VISIBLE_DEVICES", "CUDA_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES"):
            env.pop(key, None)
        if case == "gpu01_visible":
            env.update(HIP_VISIBLE_DEVICES="0,1", CUDA_VISIBLE_DEVICES="0,1")
        argv = ["python3", str(Path(__file__).resolve()), "--out", str(args.out), "--hip-child", "--case", case]
        event(args.out, f"{case}_launch")
        results.append(bounded(argv, env, args.out, case, 12))
    write(args.out / "hip_trials.json", {"cases": results, "metadata_only": True, "gpu_payload_bytes": 0,
                                        "communicators": 0, "passed_cleanup": all(r["cleanup"]["passed"] for r in results)})


def read(path):
    try:
        return Path(path).read_text().strip()
    except OSError as exc:
        return {"error": repr(exc)}


def sysfs_child(args):
    signal.signal(signal.SIGALRM, lambda signum, frame: (_ for _ in ()).throw(TimeoutError("sysfs/owner metadata deadline")))
    signal.alarm(10)
    event(args.out, "sysfs_enter")
    rows = []
    for gpu, bdf in enumerate(BDFS):
        root = Path("/sys/bus/pci/devices") / bdf
        drms = [p.name for p in Path("/sys/class/drm").glob("card[0-9]*")
                if "-" not in p.name and (p / "device").resolve() == root.resolve()]
        row = {"gpu": gpu, "bdf": bdf, "sysfs_path": str(root.resolve()), "drm_cards": drms,
               "numa_node": read(root / "numa_node"), "mem_info_vram_total": read(root / "mem_info_vram_total"),
               "mem_info_vram_used": read(root / "mem_info_vram_used"),
               "render_nodes": [p.name for p in (root / "drm").glob("renderD*")],
               "sample_monotonic_ns": time.monotonic_ns()}
        if isinstance(row["mem_info_vram_total"], str) and isinstance(row["mem_info_vram_used"], str):
            row["total_minus_used_bytes"] = int(row["mem_info_vram_total"])-int(row["mem_info_vram_used"])
        rows.append(row)
    kfd = []
    for node in Path("/sys/class/kfd/kfd/topology/nodes").glob("*"):
        properties = read(node / "properties")
        if not isinstance(properties, str):
            continue
        fields = dict(line.split(maxsplit=1) for line in properties.splitlines() if len(line.split(maxsplit=1)) == 2)
        location = int(fields.get("location_id", "0"))
        bdf = f"{int(fields.get('domain', '0')):04x}:{(location >> 8) & 255:02x}:{(location >> 3) & 31:02x}.{location & 7}"
        if bdf in BDFS:
            kfd.append({"node": node.name, "bdf_from_location_id": bdf, "gpu_id": read(node / "gpu_id"),
                        "properties": properties, "memory_banks": [{"name": p.name, "properties": read(p / "properties"),
                         "used_memory": read(p / "used_memory")} for p in (node / "mem_banks").glob("*")]})
    write(args.out / "sysfs.json", {"gpus": rows, "kfd": kfd, "sampling": "live sysfs/KFD reads; no SMI/HIP"})
    event(args.out, "sysfs_exit", selected_gpus=len(rows), selected_kfd=len(kfd))
    owners, errors = [], []
    render_paths = {"/dev/dri/" + name for row in rows for name in row["render_nodes"]}
    start = time.monotonic()
    scanned, truncated = 0, False
    for process in Path("/proc").glob("[0-9]*"):
        if time.monotonic()-start > 5:
            truncated = True
            break
        scanned += 1
        try:
            fds = set()
            for fd in (process / "fd").iterdir():
                try:
                    target = os.readlink(fd)
                    if target == "/dev/kfd" or target in render_paths:
                        fds.add(target)
                except FileNotFoundError:
                    pass
            if fds:
                owners.append({"identity": proc(int(process.name)), "comm": read(process / "comm"),
                               "cgroup": read(process / "cgroup"), "gpu_fds": sorted(fds),
                               "status_identity": [s for s in (process / "status").read_text().splitlines()
                                                   if s.startswith(("Name:", "NSpid:", "Uid:", "Gid:"))]})
        except (OSError, ValueError) as exc:
            errors.append({"pid": process.name, "error": repr(exc)})
    write(args.out / "owners.json", {"owners": owners, "errors": errors, "scanned": scanned, "truncated": truncated,
                                    "scope": "host fd/cgroup candidates; /dev/kfd alone is not GPU0/1 attribution",
                                    "complete_attribution": False})
    event(args.out, "owners_exit", owner_candidates=len(owners), errors=len(errors), truncated=truncated)


def keeper(endpoint):
    started = time.monotonic_ns()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(endpoint + "/inventory?compact=1", timeout=5) as response:
        result = json.loads(response.read())
    result["diagnosis_elapsed_ns"] = time.monotonic_ns()-started
    return result


def main(args):
    if args.out.parent != RESULT_ROOT or not args.out.name.startswith("local_xml_diagnosis_"):
        raise ValueError("require a new local_xml_diagnosis_* result directory")
    if args.hip_child:
        return hip_child(args)
    if args.trials:
        return trials(args)
    if args.sysfs_child:
        return sysfs_child(args)
    args.out.mkdir(exist_ok=False)
    args.out.chmod(0o777)
    old = RESULT_ROOT / "local_xml_node27_r01/summary.json"
    frozen = json.loads(old.read_text())
    old_preserved = all(hashlib.sha256(Path(p).read_bytes()).hexdigest() == sha for p, sha in frozen["code_sha256"].items())
    write(args.out / "registration.json", {"metadata_only": True, "gpu_payload_bytes": 0, "communicators": 0,
          "cases": ["all_visible", "gpu01_visible"], "each_once": True, "child_hard_seconds": 12,
          "cleanup_grace_seconds": 2, "only_node": "node27", "old_explicit_code_records_preserved": old_preserved,
          "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
          "old_summary_sha256": hashlib.sha256(old.read_bytes()).hexdigest()})
    if not old_preserved:
        raise ValueError("old explicit code freeze changed")
    sys_argv = ["python3", str(Path(__file__).resolve()), "--out", str(args.out), "--sysfs-child"]
    sysfs = bounded(sys_argv, dict(os.environ), args.out, "sysfs_owner", 12)
    with ThreadPoolExecutor(2) as pool:
        jobs = [pool.submit(keeper, f"http://12.11.3.27:{33000+i}") for i in range(2)]
        workers = []
        for i, job in enumerate(jobs):
            try:
                worker = job.result()
                write(args.out / f"keeper_{i}.json", worker)
                workers.append(worker)
            except Exception as exc:
                write(args.out / f"keeper_{i}.failure.json", {"error": repr(exc)})
    shell = "ulimit -c 0\nsource /opt/dtk/env.sh >/dev/null 2>&1\nexec timeout --signal=TERM --kill-after=2s 35s " + shlex.join(
        ["python3", str(Path(__file__).resolve()), "--out", str(args.out), "--trials"])
    docker = bounded(["docker", "exec", CONTAINER, "bash", "-lc", shell], dict(os.environ), args.out, "container_trials", 40)
    result = {"metadata_only": True, "gpu_payload_bytes": 0, "communicators": 0, "sysfs_process": sysfs,
              "container_process": docker, "keepers": [{"worker": w["worker"], "node": w["node"], "gpu": w["gpu"],
                "headroom_bytes": w["headroom_bytes"], "headroom_sample_ns": w["headroom_sample_ns"],
                "rpc_elapsed_ns": w["diagnosis_elapsed_ns"], "epoch": w["epoch"], "transactions": w["transactions"]} for w in workers],
              "gpu_startup_authorized": False, "admission_replaced": False}
    trials_path = args.out / "hip_trials.json"
    if trials_path.exists():
        result["hip_trials"] = json.loads(trials_path.read_text())
    if (args.out / "sysfs.json").exists():
        data = json.loads((args.out / "sysfs.json").read_text())
        result["free_comparison"] = []
        for row in data["gpus"]:
            match = next((w for w in workers if w["gpu"] == row["gpu"]), None)
            free = row.get("total_minus_used_bytes")
            result["free_comparison"].append({"gpu": row["gpu"], "bdf": row["bdf"],
                "sysfs_total_minus_used_bytes": free, "keeper_physical_free_bytes": match["headroom_bytes"]["hbm"] if match else None,
                "sysfs_minus_keeper_bytes": free-match["headroom_bytes"]["hbm"] if free is not None and match else None,
                "same_metric": False, "scope": "total-minus-used is not automatically HIP Available/free"})
    result["old_explicit_code_records_preserved"] = all(hashlib.sha256(Path(p).read_bytes()).hexdigest() == sha for p, sha in frozen["code_sha256"].items())
    result["old_raw_summary_preserved"] = hashlib.sha256(old.read_bytes()).hexdigest() == json.loads((args.out / "registration.json").read_text())["old_summary_sha256"]
    write(args.out / "summary.json", result)
    print(json.dumps({"out": str(args.out), "metadata_only": True, "gpu_payload_bytes": 0,
                      "hip_cases_recorded": trials_path.exists()}))


if __name__ == "__main__":
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=lambda p: Path(p).resolve(), required=True)
    parser.add_argument("--trials", action="store_true")
    parser.add_argument("--hip-child", action="store_true")
    parser.add_argument("--sysfs-child", action="store_true")
    parser.add_argument("--case", choices=("all_visible", "gpu01_visible"))
    main(parser.parse_args())
