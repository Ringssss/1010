"""`python -m sglang.launch_server` with FluidCKPT's loader hook installed in every TP worker.

The hook is installed at import time on purpose: SGLang spawns its workers, spawn re-imports
this file as __mp_main__ in each child, and the top-level install() runs there too.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(ROOT, "main_lib"))
from fluidckpt.serving.sglang_hook import install  # noqa: E402

install()
from fluidckpt.serving.measure import install as install_measurement  # noqa: E402
install_measurement()
from fluidckpt.serving.lifecycle import install as install_lifecycle  # noqa: E402
install_lifecycle()

if __name__ == "__main__":
    from sglang.launch_server import run_server  # noqa: E402
    from sglang.srt.plugins import load_plugins  # noqa: E402
    from sglang.srt.server_args import prepare_server_args  # noqa: E402
    from sglang.srt.utils import kill_process_tree  # noqa: E402

    load_plugins()
    try:
        run_server(prepare_server_args(sys.argv[1:]))
    finally:
        kill_process_tree(os.getpid(), include_parent=False)
