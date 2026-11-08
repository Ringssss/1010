"""CPU subprocess fixture for the init gate watchdog; never imports Torch/HIP."""
import argparse
import signal
import time

parser = argparse.ArgumentParser()
parser.add_argument("--delay", type=float, default=0)
parser.add_argument("--ignore-term", action="store_true")
args = parser.parse_args()
if args.ignore_term:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
time.sleep(args.delay)
