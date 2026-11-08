"""Test HIP virtual-address reservation sizes without allocating physical weight memory."""
import argparse
import ctypes
import ctypes.util
import json
from pathlib import Path
import resource


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--gpu', type=int, default=0)
    args = parser.parse_args()
    hip = ctypes.CDLL(ctypes.util.find_library('amdhip64') or 'libamdhip64.so')
    assert hip.hipInit(0) == 0 and hip.hipSetDevice(args.gpu) == 0
    hip.hipMemAddressReserve.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_size_t,
        ctypes.c_size_t, ctypes.c_void_p, ctypes.c_ulonglong]
    hip.hipMemAddressFree.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    rows = []
    for alignment in [0, 4096, 2 << 20]:
        for mib in [64, 128, 256, 512, 1024, 2048, 4096, 8192]:
            ptr = ctypes.c_void_p()
            status = hip.hipMemAddressReserve(ctypes.byref(ptr), mib << 20, alignment, None, 0)
            row = {'mib': mib, 'alignment': alignment, 'reserve_status': status}
            if status == 0:
                row['free_status'] = hip.hipMemAddressFree(ptr, mib << 20)
            rows.append(row)
    held = []
    cumulative = []
    for i in range(128):
        ptr = ctypes.c_void_p()
        status = hip.hipMemAddressReserve(ctypes.byref(ptr), 64 << 20, 2 << 20, None, 0)
        cumulative.append({'ordinal': i, 'reserve_status': status})
        if status: break
        held.append(ptr)
    for ptr in reversed(held):
        assert hip.hipMemAddressFree(ptr, 64 << 20) == 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({'gpu': args.gpu, 'scope': 'virtual address only, no physical allocations',
                                   'rows': rows, 'cumulative_64mib_reservations': cumulative}, indent=2)+'\n')
    print(json.dumps(rows))
    print(json.dumps({'successful_simultaneous_64mib_reservations': len(held), 'last': cumulative[-1]}))


if __name__ == '__main__':
    main()
