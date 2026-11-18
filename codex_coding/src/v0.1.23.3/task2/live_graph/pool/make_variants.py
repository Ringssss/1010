"""Make byte-distinct variants of a checkpoint (stand-ins for full fine-tunes) for the stress
catalog. Content addressing would dedup plain copies -- unfair to whole-model baselines -- so every
64 MiB chunk of every tensor file must differ: one byte per MiB of the data region is XORed with a
per-variant key; safetensors headers (and so tensor names, dtypes, shapes, offsets) are unchanged.
The weights are no longer meaningful; the restore-only experiments never run them.
  make_variants.py <src_dir> <dst_dir> <key 1..255>
Writes with fsync and drops the written pages from the page cache, so a durable read of the variant
really reads the disk."""
import os
import shutil
import struct
import sys
import time

MiB = 1 << 20
BLOCK = 64 * MiB

src, dst, key = sys.argv[1], sys.argv[2], int(sys.argv[3])
assert 1 <= key <= 255
if not any(n.endswith(".safetensors") for n in os.listdir(src)):
    sys.exit(f"no .safetensors visible in {src} on this host")
os.makedirs(dst, exist_ok=True)
t0, nbytes = time.time(), 0
for name in sorted(os.listdir(src)):
    s, d = os.path.join(src, name), os.path.join(dst, name)
    if os.path.isdir(s):
        continue
    if not name.endswith(".safetensors"):
        try:
            shutil.copyfile(s, d)
        except PermissionError:                          # e.g. root-only tokenizer files: not needed
            print({"skipped_unreadable": s}, flush=True)
        continue
    with open(s, "rb") as fi, open(d + ".tmp", "wb") as fo:
        hlen = struct.unpack("<Q", fi.read(8))[0]
        fo.write(struct.pack("<Q", hlen))
        fo.write(fi.read(hlen))
        pos = 0                                          # offset inside the data region
        while True:
            buf = bytearray(fi.read(BLOCK))
            if not buf:
                break
            first = (-pos) % MiB                         # next MiB boundary of the data region
            for i in range(first, len(buf), MiB):
                buf[i] ^= key
            fo.write(buf)
            pos += len(buf)
            nbytes += len(buf)
        fo.flush()
        os.fsync(fo.fileno())
        os.posix_fadvise(fo.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
    os.replace(d + ".tmp", d)
print({"src": src, "dst": dst, "key": key, "GiB": round(nbytes / 2**30, 2), "s": round(time.time() - t0, 1)}, flush=True)
