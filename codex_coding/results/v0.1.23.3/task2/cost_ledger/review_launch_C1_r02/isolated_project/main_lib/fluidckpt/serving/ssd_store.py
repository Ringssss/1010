"""CPU-only extent service; data and fsync target the configured local SSD path."""
import argparse
import json
import os
from pathlib import Path
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs


def serve(directory, port):
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    stats = {'read_bytes': 0, 'written_bytes': 0, 'committed_bytes': 0,
             'root': str(root), 'policy': 'buffered IO; fsync and POSIX_FADV_DONTNEED'}
    stats_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def reply(self, data, binary=False):
            payload = data if binary else json.dumps(data).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def location(self, key):
            if len(key) != 64 or any(c not in '0123456789abcdef' for c in key):
                raise ValueError('invalid extent key')
            return root / (key + '.bin')

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            path = self.location(body['key'])
            if self.path == '/create':
                with path.open('xb') as stream: stream.truncate(body['nbytes'])
            elif self.path == '/commit':
                with path.open('rb') as stream:
                    os.fsync(stream.fileno())
                    os.posix_fadvise(stream.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
                size = path.stat().st_size
                with stats_lock: stats['committed_bytes'] += size
            elif self.path == '/delete':
                path.unlink(missing_ok=True)
            else:
                raise ValueError('unknown operation')
            self.reply({'ok': True})

        def do_PUT(self):
            query = parse_qs(urlparse(self.path).query)
            data = self.rfile.read(int(self.headers['Content-Length']))
            with self.location(query['key'][0]).open('r+b', buffering=0) as stream:
                stream.seek(int(query['off'][0]))
                stream.write(data)
            with stats_lock: stats['written_bytes'] += len(data)
            self.reply({'written': len(data)})

        def do_GET(self):
            if self.path == '/status':
                kernel_io = {k: int(v.strip()) for k, v in
                             (line.split(':', 1) for line in Path('/proc/self/io').read_text().splitlines())}
                with stats_lock: snapshot = dict(stats)
                return self.reply(dict(snapshot, files=len(list(root.glob('*.bin'))), kernel_io=kernel_io))
            query = parse_qs(urlparse(self.path).query)
            off, n = int(query['off'][0]), int(query['n'][0])
            with self.location(query['key'][0]).open('rb', buffering=0) as stream:
                stream.seek(off)
                data = stream.read(n)
                os.posix_fadvise(stream.fileno(), off, n, os.POSIX_FADV_DONTNEED)
            with stats_lock: stats['read_bytes'] += len(data)
            self.reply(data, binary=True)

        def log_message(self, *args): pass

    ThreadingHTTPServer(('0.0.0.0', port), Handler).serve_forever()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', required=True)
    parser.add_argument('--port', type=int, required=True)
    args = parser.parse_args()
    serve(args.directory, args.port)
