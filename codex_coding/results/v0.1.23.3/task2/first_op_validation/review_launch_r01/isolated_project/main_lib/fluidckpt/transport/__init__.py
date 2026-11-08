"""Cross-node weight streaming: bucket many small tensors into few large collectives.

SGLang's remote weight send does one `dist.broadcast` per named_parameter. On GLM-5.3 that is
118,629 broadcasts, 58,776 of which carry a 6 KiB fp8 block-scale -- 0.0% of the payload. Every
one pays a full collective handshake. Measured on this fabric: 99 us/broadcast, so the handshake
alone is ~11.8 s, and the effective rate collapses to 1.32 GB/s (vs 25.7 GB/s for 32 MiB blocks).

This module is the alternative: pack tensors into size-bounded buckets (layer-aware, so a bucket
never straddles two layers for no reason), stream each bucket with one collective, and report
where the bytes went. See docs/findings_transport.md.
"""
from .bucket import Bucket, plan_buckets, plan_buckets_from_layout
from .stream import StreamPlan, measure_stream

__all__ = ["Bucket", "plan_buckets", "plan_buckets_from_layout", "StreamPlan", "measure_stream"]
