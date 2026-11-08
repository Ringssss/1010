"""Terminate only the recorded worker child, allowing hipprof to commit its trace DB."""
import argparse
import json
import os
from pathlib import Path
import signal
import time

from process import start_time


def stop(spec):
    state = json.loads(spec.with_suffix(".pid.json").read_text())
    parent = state["pid"]
    if start_time(parent) != state["start_time"]:
        raise RuntimeError("profiler wrapper PID no longer matches")
    matches = []
    for p in Path("/proc").glob("[0-9]*"):
        try:
            pid = int(p.name)
            args = (p/"cmdline").read_bytes().split(b"\0")
            if (os.getpgid(pid) == parent and args[0].split(b"/")[-1].startswith(b"python")
                    and b"fluidckpt.cluster.worker" in args):
                matches.append((pid, start_time(pid)))
        except (FileNotFoundError, ProcessLookupError):
            pass
    if len(matches) != 1: raise RuntimeError(f"expected one owned worker, found {matches}")
    pid, stamp = matches[0]
    if start_time(pid) != stamp: raise RuntimeError("worker PID changed")
    # Profiling pools have already been flushed at ExecuteProfile exit. Let the
    # collector observe application exit and commit before terminating its wrapper.
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic()+30
    while time.monotonic() < deadline:
        try:
            fields = Path(f"/proc/{parent}/stat").read_text().split(") ", 1)[1].split()
            if fields[0] == "Z": return
        except FileNotFoundError:
            return
        time.sleep(.1)
    raise TimeoutError("hipprof did not finish exporting after worker exit")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--spec", type=Path, required=True)
    stop(parser.parse_args().spec)
