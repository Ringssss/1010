"""GPU registration of a region whose backing is retained by a CPU-only owner."""
from .owner import Mapping, call
from .numa import placement

_known_manifests = set()


def register_manifest(path, descriptor):
    key = (path, descriptor['identity'])
    if key not in _known_manifests:
        call(path, 'put_manifest', key=descriptor['identity'],
             manifest={'meta': {name: descriptor['meta'].get(name) for name in
                       ('model_identity', 'tp_size', 'tp_rank', 'tensors')}, 'index': descriptor['index']})
        _known_manifests.add(key)


class OwnedHostAllocation:
    def __init__(self, socket_path, key, nbytes=None, numa=None, metadata=None):
        import torch
        self.mapping = Mapping(socket_path, key, nbytes, numa, metadata)
        self.nbytes = self.mapping.info['nbytes']
        self.allocated_bytes = self.mapping.info['reserved']
        self.numa = self.mapping.info['numa']
        self.backend = 'cpu_owner_registered'
        self.closed = False
        self.registered = False
        self.tensor = torch.frombuffer(self.mapping.map, dtype=torch.uint8, count=self.nbytes)
        try:
            rc = torch.cuda.cudart().cudaHostRegister(self.tensor.data_ptr(), self.allocated_bytes, 0)
            if int(rc): raise RuntimeError(f'owner-backed host registration failed: {rc}')
            self.registered = True
            self.placement = placement(self.mapping.map, samples=min(256, self.allocated_bytes//4096))
        except BaseException:
            self.tensor = None
            self.mapping.close()
            if nbytes is not None: call(socket_path, 'retire', key=key)
            raise

    def publish(self):
        self.mapping.publish()

    def info(self):
        return {'backend': self.backend, 'numa': self.numa, 'logical_bytes': self.nbytes,
                'allocation_bytes': self.allocated_bytes, 'sampled_page_distribution': self.placement,
                'owner_socket': self.mapping.path, 'owner_key': self.mapping.key}

    def close(self):
        import torch
        if self.closed: return
        if self.registered:
            torch.cuda.synchronize()
            rc = torch.cuda.cudart().cudaHostUnregister(self.tensor.data_ptr())
            if int(rc): raise RuntimeError(f'host unregister failed: {rc}')
            self.registered = False
        self.tensor = None
        self.mapping.close()
        call(self.mapping.path, 'retire', key=self.mapping.key)
        self.closed = True
