import os
for _key in ('ROCR_VISIBLE_DEVICES','CUDA_VISIBLE_DEVICES'):
    os.environ.pop(_key,None)
"""Bounded owned-context physical GPU query and CPU-only host guard extensions."""
import json
import os
from pathlib import Path
import sys
import threading
import time


def actual_device_identity(gpu):
    # Reuse the HIP BDF query used by multitier_runtime/runner/runtime_worker.py.
    import ctypes
    import ctypes.util
    hip = ctypes.CDLL(ctypes.util.find_library('amdhip64') or 'libamdhip64.so')
    buf = ctypes.create_string_buffer(64)
    rc = hip.hipDeviceGetPCIBusId(buf, len(buf), gpu)
    if rc:
        raise RuntimeError('hipDeviceGetPCIBusId failed: ' + str(rc))
    bdf = buf.value.decode().lower()
    bdf = f"{int(bdf.split(':')[0],16):04x}:" + ':'.join(bdf.split(':')[1:])
    return bdf, int((Path('/sys/bus/pci/devices') / bdf / 'numa_node').read_text())


def physical_gpu(spec):
    # An intrinsic watchdog survives loss of the docker/SSH client.
    def expire():
        time.sleep(45)
        os._exit(124)
    threading.Thread(target=expire, daemon=True).start()
    import torch
    if os.environ.get('HIP_VISIBLE_DEVICES') != '0,1,2,3,4,5,6,7':
        raise ValueError('physical ordinals require unchanged eight-card visibility')
    rows = []
    for role in spec['roles']:
        gpu = role['gpu']
        torch.cuda.set_device(gpu)
        actual_bdf, pci_numa = actual_device_identity(gpu)
        if actual_bdf != role['bdf'] or pci_numa != role['numa']:
            raise ValueError('owned physical GPU BDF/PCI NUMA differs from fresh metadata')
        free, total = torch.cuda.mem_get_info(gpu)
        rows.append(dict(gpu=gpu, bdf=role['bdf'], actual_bdf=actual_bdf, pci_numa=pci_numa, host_allocation_numa=role.get('host_numa',role['numa']), free_bytes=free, total_bytes=total,
                         required_free_bytes=spec['minimum_initial_gpu_free_bytes'],
                         used_bytes=total-free,
                         passed=free >= spec['minimum_initial_gpu_free_bytes'] and (spec['node']=='node27' or total-free <= spec['per_selected_gpu_hbm_cap_bytes'])))
    aggregate_upper = sum(r['used_bytes'] for r in rows)
    aggregate_passed = spec['node']=='node27' or aggregate_upper + (256 << 20) <= spec['task_hbm_cap_bytes']
    print(json.dumps(dict(gpus=rows, aggregate_used_upper_bytes=aggregate_upper, aggregate_cap_bytes=spec['task_hbm_cap_bytes'], aggregate_passed=aggregate_passed, passed=all(r['passed'] for r in rows) and aggregate_passed,
                         measurement='fresh owned torch.cuda.mem_get_info; zero weight/source allocation')))


def extend_host(body, spec, docker, cgroup_for):
    import hashlib
    import re
    ci = docker('/containers/' + spec['container_name'] + '/json')
    body['owned_container'] = ci
    if ci.get('Id'):
        body['owned_cgroup'] = cgroup_for(ci['State']['Pid'])
    body['source_assets'] = {}
    for item in spec.get('assets', []):
        p = Path(spec['stage']) / 'project' / item['relative_path']
        body['source_assets'][item['relative_path']] = hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
    body['task_hbm_attribution_complete'] = False
    body['task_hbm_bytes'] = None
    body['task_hbm_scope'] = 'host CPU metadata does not read driver/VRAM owners; physical upper is measured in fresh owned HIP contexts'
    return body


if __name__ == '__main__':
    physical_gpu(json.loads(sys.argv[1]))
