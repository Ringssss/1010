"""CPU subprocess fixture for the init gate watchdog; never imports Torch/HIP."""
import argparse
import json
from pathlib import Path
import signal
import time

parser = argparse.ArgumentParser()
parser.add_argument("--delay", type=float, default=0)
parser.add_argument("--ignore-term", action="store_true")
parser.add_argument("--init-event", type=Path)
parser.add_argument("--init-delay", type=float, default=0)
args = parser.parse_args()
if args.ignore_term:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
if args.init_event:
    time.sleep(args.init_delay)
    args.init_event.write_text(json.dumps({"stage": "init_admitted", "monotonic_ns": time.monotonic_ns()}) + "\n")
time.sleep(args.delay)
