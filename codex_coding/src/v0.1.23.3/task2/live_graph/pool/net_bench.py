"""Host-memory network bench: N TCP streams, each moving `--gib` GiB, no GPU involved.
  server: net_bench.py recv --port P --streams N
  client: net_bench.py send --host H --port P --streams N --gib G
Prints one JSON line with aggregate GB/s (receiver clock)."""
import argparse, json, socket, threading, time, mmap

def recv(a):
    srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", a.port)); srv.listen(64)
    conns = [srv.accept()[0] for _ in range(a.streams)]
    got = [0] * a.streams; t = [None, None]
    def one(i, c):
        c.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 16 << 20)
        buf = mmap.mmap(-1, 64 << 20); mv = memoryview(buf)
        while True:
            n = c.recv_into(mv)
            if not n: break
            got[i] += n
    t0 = time.time()
    th = [threading.Thread(target=one, args=(i, c)) for i, c in enumerate(conns)]
    [x.start() for x in th]; [x.join() for x in th]
    dt = time.time() - t0
    print(json.dumps({"streams": a.streams, "GiB": round(sum(got) / 2**30, 2), "s": round(dt, 3),
                      "GBps": round(sum(got) / dt / 1e9, 2)}), flush=True)

def send(a):
    buf = mmap.mmap(-1, 256 << 20); buf.write(b"\x5a" * (256 << 20)); mv = memoryview(buf)
    def one():
        c = socket.create_connection((a.host, a.port))
        c.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 16 << 20)
        left = int(a.gib * 2**30)
        while left > 0:
            m = min(left, len(mv)); c.sendall(mv[:m]); left -= m
        c.close()
    th = [threading.Thread(target=one) for _ in range(a.streams)]
    [x.start() for x in th]; [x.join() for x in th]

p = argparse.ArgumentParser(); p.add_argument("cmd"); p.add_argument("--host"); p.add_argument("--port", type=int, default=45555)
p.add_argument("--streams", type=int, default=1); p.add_argument("--gib", type=float, default=4)
a = p.parse_args(); (recv if a.cmd == "recv" else send)(a)
