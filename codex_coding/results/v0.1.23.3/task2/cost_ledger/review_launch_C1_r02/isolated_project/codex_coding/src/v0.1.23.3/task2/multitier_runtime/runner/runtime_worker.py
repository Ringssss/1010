"""Experiment diagnostics on the native worker; communication remains inherited."""
import argparse
import ctypes
import ctypes.util
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT / 'main_lib'))
from fluidckpt.cluster import worker as native


def runtime_identity(gpu):
    hip = ctypes.CDLL(ctypes.util.find_library('amdhip64') or 'libamdhip64.so')
    buf = ctypes.create_string_buffer(64)
    rc = hip.hipDeviceGetPCIBusId(buf, len(buf), gpu)
    if rc:
        raise RuntimeError(f'hipDeviceGetPCIBusId failed: {rc}')
    bdf = buf.value.decode().lower()
    pci = Path('/sys/bus/pci/devices') / bdf
    hca_root = Path('/sys/class/infiniband/shca_0')
    hca = dict(name='shca_0', exists=hca_root.exists(), ports={})
    if hca_root.exists():
        hca['pci_path'] = str((hca_root/'device').resolve())
        for port in (hca_root/'ports').iterdir():
            hca['ports'][port.name] = {name:(port/name).read_text().strip() for name in ('state','phys_state','rate') if (port/name).exists()}
    stat = Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()
    cgroup_path = next(line.split(':', 2)[2] for line in Path('/proc/self/cgroup').read_text().splitlines() if line.startswith('0::'))
    cgroup_root = Path('/sys/fs/cgroup') / cgroup_path.lstrip('/')
    if not (cgroup_root / 'memory.current').exists():
        # A private cgroup namespace mounts the process's cgroup as the root.
        cgroup_root = Path('/sys/fs/cgroup')
    def memory_file(name):
        path = cgroup_root / name
        if not path.exists():
            raise RuntimeError(f'cgroup v2 field missing: {name}')
        value = path.read_text().strip()
        return int(value) if value != 'max' else None
    return dict(boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                pid=os.getpid(), start_ticks=int(stat[19]), actual_bdf=bdf,
                pci_numa_node=int((pci / 'numa_node').read_text()),
                cgroup_memory_current_bytes=memory_file('memory.current'),
                cgroup_memory_max_bytes=memory_file('memory.max'),
                cgroup_path=cgroup_path, cgroup_mount_root=str(cgroup_root),
                cgroup_namespace=os.readlink('/proc/self/ns/cgroup'),
                communication_environment={key:os.environ.get(key) for key in ('NCCL_IB_HCA','NCCL_NET',
                    'NCCL_SOCKET_IFNAME','NCCL_NET_GDR_LEVEL','NCCL_TOPO_FILE','LD_PRELOAD')},
                selected_hca_metadata=hca, actual_hca_transport_requires_nccl_logs=True,
                process_rss_bytes=int(Path('/proc/self/statm').read_text().split()[1]) * os.sysconf('SC_PAGE_SIZE'))


class RuntimeWorker(native.ClusterWorker):
    def __init__(self, args):
        self.host_allocations = []
        super().__init__(args)

    def allocate_host(self, nbytes, numa=None):
        owner = super().allocate_host(nbytes, numa)
        self.host_allocations.append(dict(owner.info(), registered=owner.registered))
        return owner

    def allocate(self, desc):
        tensor, owner = super().allocate(desc)
        if desc['tier'] == 'dram':
            self.host_allocations.append(dict(owner.info(), registered=owner.registered, id=desc['id']))
        return tensor, owner

    def synth(self, snap, tp, nbytes, seed):
        result = super().synth(snap, tp, nbytes, seed)
        owner = self.shards[snap, tp].owner
        return dict(result, host_allocation=dict(owner.info(), registered=owner.registered))

    def inventory(self, compact=False):
        result = super().inventory(compact)
        result.update(runtime_identity=runtime_identity(self.gpu), host_allocations=self.host_allocations,
                      host_allocations_scope='historical successful allocations; not a current residency ledger')
        return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--node', required=True)
    parser.add_argument('--gpu', required=True, type=int)
    parser.add_argument('--port-base', required=True, type=int)
    parser.add_argument('--mesh', required=True)
    parser.add_argument('--master', required=True)
    parser.add_argument('--handoff', required=True)
    parser.add_argument('--session', required=True)
    parser.add_argument('--numa', required=True, type=int)
    parser.add_argument('--dram-budget', type=int, default=64 << 20)
    parser.add_argument('--hbm-budget', type=int, default=64 << 20)
    parser.add_argument('--ssd-budget', type=int, default=64 << 20)
    parser.set_defaults(memory_backend='numa_registered', replica_budget_bytes=64 << 20)
    native.ClusterWorker = RuntimeWorker
    native.main(parser.parse_args())


if __name__ == '__main__':
    main()
