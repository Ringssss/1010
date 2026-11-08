"""Fluid bandwidth sharing: max-min fair rates and an event-driven completion simulator."""
import math

INF = float("inf")


def maxmin_rates(flows, capacity):
    """flows: list of (cap, [resource names]); capacity: {resource: GB/s} (missing = unbounded).

    Progressive filling: raise all unfrozen flows together; freeze a flow when it hits its
    own cap or when any resource it uses saturates.
    """
    n = len(flows)
    rate = [0.0] * n
    frozen = [False] * n
    residual = {r: capacity.get(r, INF) for f in flows for r in f[1]}
    while not all(frozen):
        users = {}
        for i, (_, res) in enumerate(flows):
            if not frozen[i]:
                for r in res:
                    users[r] = users.get(r, 0) + 1
        step = min([flows[i][0] - rate[i] for i in range(n) if not frozen[i]] +
                   [residual[r] / u for r, u in users.items() if residual[r] < INF])
        step = max(step, 0.0)
        for i in range(n):
            if not frozen[i]:
                rate[i] += step
        for r, u in users.items():
            if residual[r] < INF:
                residual[r] -= step * u
        for i, (cap, res) in enumerate(flows):
            if frozen[i]:
                continue
            if rate[i] >= cap - 1e-9 or any(residual[r] <= 1e-9 for r in res):
                frozen[i] = True
    return rate


def simulate(streams, capacity):
    """streams: list of (cap, [resources], bytes). Each stream moves its bytes at the max-min
    fair rate, re-evaluated whenever some stream finishes. Returns finish time per stream (s)."""
    left = [s[2] / 1e9 for s in streams]  # GB
    finish = [0.0 if b <= 0 else None for b in left]
    now = 0.0
    while any(f is None for f in finish):
        act = [i for i, f in enumerate(finish) if f is None]
        rates = maxmin_rates([(streams[i][0], streams[i][1]) for i in act], capacity)
        dt = min(left[i] / r if r > 0 else INF for i, r in zip(act, rates))
        if math.isinf(dt):
            raise RuntimeError("stalled stream: zero rate")
        now += dt
        for i, r in zip(act, rates):
            left[i] -= r * dt
            if left[i] <= 1e-12:
                finish[i] = now
    return finish
