"""SGLang load boundaries shared by baseline and restore runs; no installed-source edits."""
import functools
import json
import os
from pathlib import Path
import socket
import time


def emit(rank, event, **fields):
    directory = os.environ.get("FLUIDCKPT_METRICS")
    emit_to(directory, rank, event, **fields)


def emit_to(directory, rank, event, **fields):
    """Record events from the model and its local keeper on the same host clock."""
    if not directory:
        return
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    row = dict(event=event, rank=rank, pid=os.getpid(), host=socket.gethostname(),
               monotonic_ns=time.monotonic_ns(), wall_ns=time.time_ns(), **fields)
    with open(path / f"rank{rank}.jsonl", "a") as stream:
        stream.write(json.dumps(row) + "\n")


def install():
    if not os.environ.get("FLUIDCKPT_METRICS"):
        return
    import torch
    from sglang.srt.model_executor.model_runner import ModelRunner
    original = ModelRunner.load_model
    if getattr(original, "_fluidckpt_measured", False):
        return

    @functools.wraps(original)
    def measured(self, *args, **kwargs):
        rank = self.tp_rank
        emit(rank, "load_begin")
        try:
            result = original(self, *args, **kwargs)
            torch.cuda.synchronize()
        except BaseException as exc:
            emit(rank, "load_failed", error=repr(exc))
            raise
        emit(rank, "weights_ready")
        return result

    measured._fluidckpt_measured = True
    ModelRunner.load_model = measured


def summarize(directory, ranks):
    rows = {}
    for rank in range(ranks):
        events = [json.loads(line) for line in (Path(directory) / f"rank{rank}.jsonl").read_text().splitlines()]
        if any(e["event"] == "load_failed" for e in events):
            raise RuntimeError(f"rank {rank} failed to load")
        begin = [e for e in events if e["event"] == "load_begin"]
        end = [e for e in events if e["event"] == "weights_ready"]
        if len(begin) != 1 or len(end) != 1 or end[0]["monotonic_ns"] < begin[0]["monotonic_ns"]:
            raise ValueError(f"invalid loading boundaries for rank {rank}")
        if any(e["host"] != begin[0]["host"] for e in events):
            raise ValueError(f"rank {rank} contains events from different hosts")
        rows[str(rank)] = {"begin_ns": begin[0]["monotonic_ns"], "end_ns": end[0]["monotonic_ns"],
                           "host": begin[0]["host"], "events": events}
    if len({r["host"] for r in rows.values()}) != 1:
        raise ValueError("cross-host monotonic timestamps cannot be subtracted")
    return {"load_s": (max(r["end_ns"] for r in rows.values()) - min(r["begin_ns"] for r in rows.values())) / 1e9,
            "arrival_skew_s": (max(r["begin_ns"] for r in rows.values()) - min(r["begin_ns"] for r in rows.values())) / 1e9,
            "rank_s": {k: (r["end_ns"] - r["begin_ns"]) / 1e9 for k, r in rows.items()}, "ranks": rows}
