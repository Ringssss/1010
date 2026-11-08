"""Exercise the lifecycle runner's node28 SSD service with real remote byte traffic."""
import argparse
import hashlib
import json
import shlex
import subprocess
import urllib.request

from run_lifecycle import LifecycleRun, ROOT
from fluidckpt.cluster.control import request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True)
    args = parser.parse_args()
    args.resume_stage = None
    args.port_base = 29500
    args.ssd_node = 'node28'
    args.ssd_directory = None
    args.result_root = str(ROOT/'codex_coding/results/ssd_probe')
    runner = LifecycleRun(args)
    key = hashlib.sha256(args.run.encode()).hexdigest()
    created = False
    try:
        runner.start_ssd()
        endpoint = runner.ssd_endpoint
        data = bytes(range(256))*65536
        request(endpoint, 'create', {'key': key, 'nbytes': len(data)})
        created = True
        for offset in [0, len(data)//2]:
            req = urllib.request.Request(f'{endpoint}/write?key={key}&off={offset}',
                data=data[offset:offset+len(data)//2], method='PUT')
            with urllib.request.urlopen(req, timeout=30) as reply:
                assert json.load(reply)['written'] == len(data)//2
        request(endpoint, 'commit', {'key': key})
        with urllib.request.urlopen(f'{endpoint}/read?key={key}&off=0&n={len(data)}', timeout=30) as reply:
            actual = reply.read()
        assert hashlib.sha256(actual).digest() == hashlib.sha256(data).digest()
        runner.result['ssd_stats'] = request(endpoint, 'status')
        runner.result['sha256'] = hashlib.sha256(actual).hexdigest()
        runner.result['status'] = 'passed'
        runner.event('roundtrip_passed', nbytes=len(data), ssd_node=args.ssd_node)
    except Exception as exc:
        runner.result['status'] = 'failed'
        runner.event('failed', error=repr(exc))
        raise
    finally:
        if created:
            request(runner.ssd_endpoint, 'delete', {'key': key})
        runner.cleanup()
        if args.ssd_directory:
            subprocess.run(['ssh', args.ssd_node, shlex.join(['rmdir', args.ssd_directory])], check=True)


if __name__ == '__main__':
    main()
