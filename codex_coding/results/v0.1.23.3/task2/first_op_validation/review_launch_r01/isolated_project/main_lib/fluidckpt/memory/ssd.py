"""Bounded HTTP access to immutable extents on a remote node's local SSD."""
import hashlib
import json
import urllib.parse
import urllib.request


class RemoteExtent:
    def __init__(self, endpoint, key, nbytes):
        self.endpoint = endpoint.rstrip('/')
        self.key = hashlib.sha256(key.encode()).hexdigest()
        self.nbytes = nbytes
        self.read_bytes = self.write_bytes = 0
        self.call('create', {'key': self.key, 'nbytes': nbytes})

    def call(self, op, body):
        req = urllib.request.Request(self.endpoint+'/'+op, data=json.dumps(body).encode(),
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=180) as response:
            return json.load(response)

    def read(self, off, n):
        import torch
        query = urllib.parse.urlencode({'key': self.key, 'off': off, 'n': n})
        with urllib.request.urlopen(self.endpoint+'/read?'+query, timeout=180) as response:
            data = bytearray(response.read())
        if len(data) != n: raise IOError('short SSD read')
        self.read_bytes += n
        return torch.frombuffer(data, dtype=torch.uint8)

    def write(self, off, tensor):
        data = tensor.cpu().numpy().tobytes()
        query = urllib.parse.urlencode({'key': self.key, 'off': off})
        req = urllib.request.Request(self.endpoint+'/write?'+query, data=data, method='PUT')
        with urllib.request.urlopen(req, timeout=180) as response:
            response.read()
        self.write_bytes += len(data)

    def commit(self):
        return self.call('commit', {'key': self.key})

    def close(self):
        self.call('delete', {'key': self.key})
