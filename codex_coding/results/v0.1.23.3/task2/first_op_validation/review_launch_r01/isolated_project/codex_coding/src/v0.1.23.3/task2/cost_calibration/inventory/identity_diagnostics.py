"""Time the original identity sources, optionally reusing only the library handle."""
import ctypes
import ctypes.util
import os
from pathlib import Path
import time


class Segments:
    def __init__(self):
        self.values = {}

    def measure(self, name, function):
        start = time.monotonic_ns()
        try:
            return function()
        finally:
            self.values[name] = self.values.get(name, 0) + time.monotonic_ns() - start


class LibraryCache:
    def __init__(self):
        self.pid = None
        self.library = None
        self.path = None
        self.origin_sequence = None

    def get(self, mode, timings, sequence):
        pid = os.getpid()
        hit = mode == 'cached' and self.pid == pid and self.library is not None
        if hit:
            path = timings.measure('library_resolution', lambda: self.path)
            library = timings.measure('cdll_load', lambda: self.library)
        else:
            path = timings.measure('library_resolution',
                                   lambda: ctypes.util.find_library('amdhip64') or 'libamdhip64.so')
            library = timings.measure('cdll_load', lambda: ctypes.CDLL(path))
            self.pid, self.path, self.library = pid, path, library
            self.origin_sequence = sequence
        return library, hit


def runtime_identity(gpu, mode, timings, cache, sequence):
    hip, cache_hit = cache.get(mode, timings, sequence)
    def hip_bdf():
        buf = ctypes.create_string_buffer(64)
        rc = hip.hipDeviceGetPCIBusId(buf, len(buf), gpu)
        if rc:
            raise RuntimeError(f'hipDeviceGetPCIBusId failed: {rc}')
        return buf.value.decode().lower()
    bdf = timings.measure('hip_bdf', hip_bdf)
    pci = Path('/sys/bus/pci/devices') / bdf
    def hca_metadata():
        root = Path('/sys/class/infiniband/shca_0')
        hca = dict(name='shca_0', exists=root.exists(), ports={})
        if root.exists():
            hca['pci_path'] = str((root / 'device').resolve())
            for port in (root / 'ports').iterdir():
                hca['ports'][port.name] = {
                    name: (port / name).read_text().strip()
                    for name in ('state', 'phys_state', 'rate') if (port / name).exists()}
        return hca
    hca = timings.measure('hca_metadata', hca_metadata)
    stat = timings.measure('proc_identity',
                           lambda: Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split())
    def cgroup_metadata():
        path = next(line.split(':', 2)[2]
                    for line in Path('/proc/self/cgroup').read_text().splitlines()
                    if line.startswith('0::'))
        root = Path('/sys/fs/cgroup') / path.lstrip('/')
        if not (root / 'memory.current').exists():
            root = Path('/sys/fs/cgroup')
        def memory_file(name):
            location = root / name
            if not location.exists():
                raise RuntimeError(f'cgroup v2 field missing: {name}')
            value = location.read_text().strip()
            return int(value) if value != 'max' else None
        return dict(cgroup_path=path, cgroup_mount_root=str(root),
                    cgroup_namespace=os.readlink('/proc/self/ns/cgroup'),
                    cgroup_memory_current_bytes=memory_file('memory.current'),
                    cgroup_memory_max_bytes=memory_file('memory.max'))
    cgroup = timings.measure('cgroup_metadata', cgroup_metadata)
    boot_id = timings.measure('proc_identity',
                              lambda: Path('/proc/sys/kernel/random/boot_id').read_text().strip())
    pci_numa = timings.measure('pci_metadata', lambda: int((pci / 'numa_node').read_text()))
    def environment_and_rss():
        return dict(communication_environment={key: os.environ.get(key) for key in
                    ('NCCL_IB_HCA', 'NCCL_NET', 'NCCL_SOCKET_IFNAME', 'NCCL_NET_GDR_LEVEL',
                     'NCCL_TOPO_FILE', 'LD_PRELOAD')},
                    process_rss_bytes=int(Path('/proc/self/statm').read_text().split()[1])
                    * os.sysconf('SC_PAGE_SIZE'))
    environment = timings.measure('environment_and_rss', environment_and_rss)
    result = dict(boot_id=boot_id, pid=os.getpid(), start_ticks=int(stat[19]), actual_bdf=bdf,
                  pci_numa_node=pci_numa, selected_hca_metadata=hca,
                  actual_hca_transport_requires_nccl_logs=True, **cgroup, **environment)
    return result, cache_hit
