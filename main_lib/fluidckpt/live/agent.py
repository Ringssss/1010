"""Per-node probe publisher and the cluster-side aggregator that feeds a LiveGraph.

Publisher (one per node, stdlib only): samples fast state every `fast_s` and PCIe every ~1 s on
a background thread, keeps the latest of each with a version counter, and serves
  GET /inventory               static inventory
  GET /state?since=<version>   {"version", "fast", "pcie"} -- only parts newer than `since`
so a puller that is up to date receives an empty body cheaply.

Aggregator (on the planning node): pulls every publisher concurrently, feeds new samples to the
LiveGraph, and marks a node stale when it stops answering (the graph's snapshot derates stale
state). It also accepts one-shot probe JSON, so peers can be read over ssh without a daemon.
"""
import json
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .probe import NodeProbe


class Publisher:
    def __init__(self, node=None, fast_s=0.1, pcie=True):
        self.probe = NodeProbe(node)
        self.inventory = self.probe.inventory()
        self.fast_s, self.lock, self.stop = fast_s, threading.Lock(), threading.Event()
        self.version, self.fast, self.pcie = 0, None, None
        self.fast_v = self.pcie_v = 0
        self.threads = [threading.Thread(target=self._fast_loop, daemon=True)]
        if pcie and self.inventory["gpus"]:
            self.threads.append(threading.Thread(target=self._pcie_loop, daemon=True))

    def _publish(self, kind, sample):
        with self.lock:
            self.version += 1
            setattr(self, kind, sample)
            setattr(self, kind + "_v", self.version)

    def _fast_loop(self):
        self.probe.sample_fast()                       # prime rate counters
        while not self.stop.wait(self.fast_s):
            self._publish("fast", self.probe.sample_fast())

    def _pcie_loop(self):
        while not self.stop.is_set():
            self._publish("pcie", self.probe.sample_pcie())   # blocks ~1 s

    def state(self, since=0):
        with self.lock:
            return {"version": self.version,
                    "fast": self.fast if self.fast_v > since else None,
                    "pcie": self.pcie if self.pcie_v > since else None}

    def serve(self, port, host="0.0.0.0"):
        pub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                url = urlparse(self.path)
                if url.path == "/inventory":
                    body = pub.inventory
                elif url.path == "/state":
                    body = pub.state(int(parse_qs(url.query).get("since", ["0"])[0]))
                else:
                    self.send_error(404)
                    return
                data = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        for t in self.threads:
            t.start()
        self.server = ThreadingHTTPServer((host, port), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self.server

    def close(self):
        self.stop.set()
        if getattr(self, "server", None):
            self.server.shutdown()
            self.server.server_close()


def _get(url, timeout):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


class Aggregator:
    """Keeps a LiveGraph current from several publishers ({node: "http://host:port"})."""

    def __init__(self, graph, endpoints, timeout=0.5):
        self.graph, self.endpoints, self.timeout = graph, dict(endpoints), timeout
        self.since = {n: 0 for n in endpoints}
        self.errors = {}

    def ingest_oneshot(self, probe_json):
        """Feed the output of `python3 probe.py` (e.g. collected over ssh)."""
        node = probe_json["inventory"]["node"]
        for f in probe_json.get("fast") or []:
            self.graph.ingest(node, fast=f)
        if probe_json.get("pcie"):
            self.graph.ingest(node, pcie=probe_json["pcie"])

    def poll(self):
        """One concurrent pull of every endpoint; returns {node: seconds} pull latency."""
        lat, results = {}, {}

        def pull(node, base):
            t = time.perf_counter()
            try:
                results[node] = _get(f"{base}/state?since={self.since[node]}", self.timeout)
                self.errors.pop(node, None)
            except Exception as e:                      # unreachable peer -> state goes stale
                self.errors[node] = repr(e)
            lat[node] = time.perf_counter() - t

        threads = [threading.Thread(target=pull, args=kv) for kv in self.endpoints.items()]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        for node, body in results.items():
            if body["fast"] or body["pcie"]:
                self.graph.ingest(node, fast=body["fast"], pcie=body["pcie"])
            self.since[node] = body["version"]
        return lat


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--node")
    p.add_argument("--port", type=int, default=18740)
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--duration", type=float, default=0, help="exit after N seconds (0 = forever)")
    a = p.parse_args(argv)
    pub = Publisher(a.node)
    pub.serve(a.port, a.host)
    try:
        if a.duration:
            time.sleep(a.duration)
        else:
            threading.Event().wait()
    finally:
        pub.close()


if __name__ == "__main__":
    main()
