"""CPU-only ownership of memfd-backed immutable regions; GPU clients hold revocable leases."""
import array
import ctypes
import json
import mmap
import os
from pathlib import Path
import socket
import socketserver
import struct
import threading
import time
import uuid


def process_stamp(pid):
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().split(') ', 1)[1].split()
        return None if fields[0] == 'Z' else fields[19]
    except FileNotFoundError:
        return None


def receive_exact(sock, n):
    data = bytearray()
    while len(data) < n:
        part = sock.recv(n-len(data))
        if not part:
            raise EOFError('owner socket closed')
        data.extend(part)
    return bytes(data)


def call(path, operation, **body):
    payload = json.dumps(dict(operation=operation, **body)).encode()
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(60)
        deadline = time.monotonic()+60
        while True:
            try:
                sock.connect(str(path))
                break
            except BlockingIOError:
                if time.monotonic() >= deadline: raise
                time.sleep(.01)
        sock.sendall(struct.pack('!I', len(payload))+payload)
        header, ancillary, _, _ = sock.recvmsg(4, socket.CMSG_SPACE(4), socket.MSG_WAITALL)
        if len(header) != 4:
            raise EOFError('owner did not return a response header')
        result = json.loads(receive_exact(sock, struct.unpack('!I', header)[0]))
        descriptor = None
        for level, kind, data in ancillary:
            if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                descriptors = array.array('i'); descriptors.frombytes(data[:4])
                descriptor = descriptors[0]
                os.set_inheritable(descriptor, False)
        if 'error' in result:
            if descriptor is not None: os.close(descriptor)
            raise RuntimeError(result['error'])
        return result, descriptor


class Store:
    def __init__(self, budget, free_page_guard_bytes=0):
        self.budget = budget
        self.free_page_guard_bytes = free_page_guard_bytes
        self.entries = {}
        self.manifests = {}
        self.leases = {}
        self.lock = threading.RLock()

    def reap(self):
        for lease, row in list(self.leases.items()):
            if row['stamp'] is None or process_stamp(row['pid']) != row['stamp']:
                del self.leases[lease]
        for key, row in list(self.entries.items()):
            if row['state'] == 'RETIRING' and not any(v['key'] == key for v in self.leases.values()):
                row['mapping'].close(); os.close(row['fd']); del self.entries[key]

    def dispatch(self, body, pid):
        with self.lock:
            self.reap()
            operation = body['operation']
            key = body.get('key')
            if operation == 'put_manifest':
                if key in self.manifests and self.manifests[key] != body['manifest']:
                    raise ValueError('manifest identity was reused for different metadata')
                self.manifests[key] = body['manifest']
                return {'ok': True}, None
            if operation == 'get_manifest':
                return self.manifests[key], None
            if operation == 'allocate':
                if key in self.entries:
                    raise ValueError('region already exists')
                n = body['nbytes']
                reserved = (n+4095)//4096*4096
                if n <= 0 or reserved+sum(v['reserved'] for v in self.entries.values()) > self.budget:
                    raise MemoryError('CPU owner budget exceeded')
                if self.free_page_guard_bytes and body.get('numa') is not None:
                    from fluidckpt.topology.discover import memory_node
                    free = memory_node(body['numa']).get('MemFree', 0)
                    if free < reserved+self.free_page_guard_bytes:
                        raise MemoryError('NUMA free-page reserve would be crossed; select another binding')
                fd = os.memfd_create('fluidckpt-region', os.MFD_CLOEXEC)
                mapping = None
                try:
                    os.ftruncate(fd, reserved)
                    mapping = mmap.mmap(fd, reserved, flags=mmap.MAP_SHARED)
                    if body.get('numa') is not None:
                        from .numa import mbind
                        mbind(mapping, body['numa'], move=False)
                    ctypes.memset(ctypes.addressof(ctypes.c_char.from_buffer(mapping)), 0, reserved)
                except BaseException:
                    if mapping is not None: mapping.close()
                    os.close(fd)
                    raise
                self.entries[key] = {'fd': fd, 'mapping': mapping, 'nbytes': n,
                    'reserved': reserved, 'numa': body.get('numa'), 'state': 'BUILDING',
                    'metadata': body.get('metadata', {})}
            if operation in ['allocate', 'acquire']:
                entry = self.entries[key]
                if operation == 'acquire' and entry['state'] != 'VALID':
                    raise ValueError('region is not published')
                lease = uuid.uuid4().hex
                self.leases[lease] = {'key': key, 'pid': pid, 'stamp': process_stamp(pid)}
                return {'key': key, 'lease': lease, 'nbytes': entry['nbytes'],
                        'reserved': entry['reserved'], 'numa': entry['numa']}, entry['fd']
            if operation == 'publish':
                entry = self.entries[key]
                entry['state'] = 'VALID'
            elif operation == 'release':
                self.leases.pop(body['lease'], None)
                self.reap()
            elif operation == 'retire':
                self.entries[key]['state'] = 'RETIRING'
                self.reap()
            elif operation == 'inventory':
                return {'pid': os.getpid(), 'budget': self.budget,
                    'used_bytes': sum(v['reserved'] for v in self.entries.values()),
                    'leases': len(self.leases),
                    'regions': [{k: v for k, v in entry.items() if k not in ['fd', 'mapping']}
                                | {'key': name} for name, entry in self.entries.items()]}, None
            elif operation != 'allocate':
                raise ValueError('unknown owner operation')
            return {'ok': True}, None


def serve(path, budget, recover_directory=None, free_page_guard_bytes=0):
    store = Store(budget, free_page_guard_bytes)
    if recover_directory:
        for report_path in sorted(Path(recover_directory).glob('worker*.json')):
            report = json.loads(report_path.read_text())
            if process_stamp(report['pid']) != report['stamp']:
                raise RuntimeError('recovery client has exited or changed PID')
            for key, manifest in report['manifests'].items():
                if key in store.manifests and store.manifests[key] != manifest:
                    raise ValueError('recovery manifests disagree')
                store.manifests[key] = manifest
            for region in report['regions']:
                desc = region['metadata']; key = desc['id']
                if key in store.entries: raise ValueError('duplicate recovery region')
                reserved = (desc['n']+4095)//4096*4096
                if reserved+sum(v['reserved'] for v in store.entries.values()) > budget:
                    raise MemoryError('recovered allocations exceed owner budget')
                fd = os.open(f"/proc/{report['pid']}/fd/{region['fd']}", os.O_RDWR | os.O_CLOEXEC)
                stat = os.fstat(fd)
                if stat.st_ino != region['inode'] or stat.st_size != reserved:
                    os.close(fd)
                    raise RuntimeError('recovery descriptor changed')
                mapping = mmap.mmap(fd, reserved, flags=mmap.MAP_SHARED)
                store.entries[key] = {'fd': fd, 'mapping': mapping, 'nbytes': desc['n'],
                    'reserved': reserved, 'numa': desc['numa'], 'state': 'VALID', 'metadata': desc}
                store.leases[region['lease']] = {'key': key, 'pid': report['pid'], 'stamp': report['stamp']}
        print(json.dumps({'event': 'owner_recovered', 'regions': len(store.entries),
                          'bytes': sum(e['nbytes'] for e in store.entries.values())}), flush=True)
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            fd = None
            try:
                pid, _, _ = struct.unpack('3i', self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                body = json.loads(receive_exact(self.request, struct.unpack('!I', receive_exact(self.request, 4))[0]))
                result, fd = store.dispatch(body, pid)
            except Exception as exc:
                result = {'error': repr(exc)}
            payload = json.dumps(result).encode()
            ancillary = [] if fd is None else [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array('i', [fd]))]
            self.request.sendmsg([struct.pack('!I', len(payload))], ancillary)
            self.request.sendall(payload)
    class Server(socketserver.ThreadingUnixStreamServer):
        daemon_threads = True
        request_queue_size = 128
    with Server(str(path), Handler) as server:
        os.chmod(path, 0o600)
        server.serve_forever()


class Mapping:
    def __init__(self, path, key, nbytes=None, numa=None, metadata=None):
        self.path, self.key = str(path), key
        args = {'key': key}
        if nbytes is not None:
            args.update(nbytes=nbytes, numa=numa, metadata=metadata or {})
        self.info, self.fd = call(path, 'allocate' if nbytes is not None else 'acquire', **args)
        self.map = mmap.mmap(self.fd, self.info['reserved'], flags=mmap.MAP_SHARED)
        self.closed = False

    def publish(self):
        call(self.path, 'publish', key=self.key)

    def close(self):
        if self.closed:
            return
        self.map.close(); os.close(self.fd)
        call(self.path, 'release', lease=self.info['lease'])
        self.closed = True


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--socket', required=True)
    parser.add_argument('--budget-bytes', type=int, required=True)
    parser.add_argument('--recover-directory')
    parser.add_argument('--free-page-guard-bytes', type=int, default=0)
    options = parser.parse_args()
    serve(options.socket, options.budget_bytes, options.recover_directory, options.free_page_guard_bytes)
