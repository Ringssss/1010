"""baseline_sglang.py — SGLang keep-alive baseline (release/resume memory occupation).

Measures one SGLang engine on ONE card: HBM before release, after release, after resume, and the
wall time of each of the two calls. One JSON line per measurement (repo convention: "event" key).

This is the baseline we must beat/mirror: SGLang keeps ONE model's weights resident on ONE machine
and parks them with `release_memory_occupation` / `resume_memory_occupation`. On a CUDA build the
park/unpark is done by `torch_memory_saver` (VA unmapped, physical pages retained or backed up to
CPU). If torch_memory_saver is absent the adapter is a NO-OP: release returns instantly, HBM does
not move, and the model is still resident. So the HBM delta across release is itself the verdict.

Run inside the GPU container, one card only:
  HIP_VISIBLE_DEVICES=4 python baseline_sglang.py --model /data/.../9g_8b_thinking_llama
"""
import argparse
import json
import os
import sys
import time
import traceback

GiB = 1 << 30


def emit(**kw):
    print(json.dumps(kw, default=str), flush=True)


def hbm(tag):
    """One HBM sample. free/total come from the driver; allocated/reserved are torch-level."""
    import torch

    free, total = torch.cuda.mem_get_info(0)
    emit(
        event="hbm",
        phase=tag,
        free_gib=round(free / GiB, 3),
        used_gib=round((total - free) / GiB, 3),
        total_gib=round(total / GiB, 3),
        torch_allocated_gib=round(torch.cuda.memory_allocated(0) / GiB, 3),
        torch_reserved_gib=round(torch.cuda.memory_reserved(0) / GiB, 3),
        t=round(time.time(), 3),
    )


def probe_env():
    """Report the things that decide whether the keep-alive path can work at all on this stack."""
    import torch

    info = {
        "event": "env",
        "torch": torch.__version__,
        "torch_hip": torch.version.hip,
        "torch_cuda": torch.version.cuda,
        "device_name": torch.cuda.get_device_name(0),
        "device_count": torch.cuda.device_count(),
    }
    try:
        import sglang

        info["sglang"] = getattr(sglang, "__version__", "unknown")
    except Exception as e:  # noqa: BLE001
        info["sglang_error"] = repr(e)
    try:
        import torch_memory_saver

        info["torch_memory_saver"] = getattr(torch_memory_saver, "__file__", "present")
    except Exception as e:  # noqa: BLE001
        info["torch_memory_saver"] = None
        info["torch_memory_saver_error"] = repr(e)
    try:
        from sglang.srt.platforms import current_platform as p

        info["sglang_platform"] = type(p).__name__
        info["sglang_device_type"] = getattr(p, "device_type", None)
        info["sglang_device_name"] = getattr(p, "device_name", None)
    except Exception as e:  # noqa: BLE001
        info["sglang_platform_error"] = repr(e)
    emit(**info)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, help="HF model dir visible inside the container")
    ap.add_argument("--gpu", type=int, default=0, help="container-visible ordinal (HIP_VISIBLE_DEVICES)")
    ap.add_argument("--mem-fraction", type=float, default=0.35, help="static memory fraction")
    ap.add_argument("--context-length", type=int, default=2048)
    ap.add_argument("--disable-cuda-graph", action="store_true", default=True)
    ap.add_argument("--tags", default=None, help="comma list of tags; default = all")
    # The keep-alive path needs torch_memory_saver. Without it, enable_memory_saver=True aborts the
    # engine at startup (engine.py:586). --no-memory-saver measures what release/resume do when the
    # adapter is the Noop one, which is the only variant that can start on this stack.
    ap.add_argument("--no-memory-saver", action="store_true",
                    help="construct the engine without enable_memory_saver (Noop adapter)")
    a = ap.parse_args()

    import torch

    torch.cuda.set_device(a.gpu)

    emit(event="start", model=a.model, gpu=a.gpu, pid=os.getpid())
    probe_env()
    hbm("pre_engine")

    Engine = None
    last_err = None
    # Engine construction can fail for import/platform reasons; record and stop cleanly.
    try:
        from sglang import Engine as _E

        Engine = _E
    except Exception:
        emit(event="engine_import_failed", traceback=traceback.format_exc())
        return 2

    t0 = time.time()
    try:
        engine = Engine(
            model_path=a.model,
            tp_size=1,
            base_gpu_id=a.gpu,
            mem_fraction_static=a.mem_fraction,
            context_length=a.context_length,
            disable_cuda_graph=a.disable_cuda_graph,
            enable_memory_saver=not a.no_memory_saver,
            log_level="info",
        )
    except Exception:
        emit(
            event="engine_start_failed",
            seconds=round(time.time() - t0, 3),
            traceback=traceback.format_exc(),
        )
        hbm("post_engine_fail")
        return 3

    emit(event="engine_started", seconds=round(time.time() - t0, 3))
    hbm("post_engine")

    tags = a.tags.split(",") if a.tags else None
    emit(event="release_begin", tags=tags)
    try:
        t = time.time()
        engine.release_memory_occupation(tags=tags)
        rel_dt = time.time() - t
        emit(event="release_done", seconds=round(rel_dt, 4))
    except Exception:
        emit(event="release_failed", traceback=traceback.format_exc())
        rel_dt = None
    hbm("post_release")

    emit(event="resume_begin", tags=tags)
    try:
        t = time.time()
        engine.resume_memory_occupation(tags=tags)
        res_dt = time.time() - t
        emit(event="resume_done", seconds=round(res_dt, 4))
    except Exception:
        emit(event="resume_failed", traceback=traceback.format_exc())
        res_dt = None
    hbm("post_resume")

    emit(
        event="summary",
        release_seconds=round(rel_dt, 4) if rel_dt is not None else None,
        resume_seconds=round(res_dt, 4) if res_dt is not None else None,
        model=a.model,
        gpu=a.gpu,
    )
    try:
        engine.shutdown()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
