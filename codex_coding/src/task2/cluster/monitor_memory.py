"""Sample physical DCU memory via sysfs without creating a HIP context."""
import argparse
import json
from pathlib import Path
import socket
import time

parser = argparse.ArgumentParser()
parser.add_argument('--out', type=Path, required=True)
parser.add_argument('--interval', type=float, default=1)
args = parser.parse_args()
with args.out.open('a', buffering=1) as stream:
    while True:
        devices = {}
        for path in Path('/sys/class/drm').glob('card[0-9]*/device/mem_info_vram_used'):
            devices[path.parts[-3]] = int(path.read_text())
        stream.write(json.dumps({'wall_ns': time.time_ns(), 'monotonic_ns': time.monotonic_ns(),
                                 'host': socket.gethostname(), 'vram_used_bytes': devices})+'\n')
        time.sleep(args.interval)
