"""Ask the kernel to drop the clean page cache of the model files (we have no root to
drop_caches): posix_fadvise(DONTNEED) per file. usage: drop_cache.py <dir> [<dir> ...]"""
import os, sys
n = 0
for d in sys.argv[1:]:
    for root, _, files in os.walk(d, followlinks=True):
        for f in files:
            try:
                fd = os.open(os.path.join(root, f), os.O_RDONLY); os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED); os.close(fd); n += 1
            except OSError:
                pass
print("fadvise DONTNEED on", n, "files")
