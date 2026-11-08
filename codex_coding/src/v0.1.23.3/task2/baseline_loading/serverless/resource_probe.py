"""Read cgroup/host memory without importing Torch or opening any GPU."""
import json
from pathlib import Path
import resource

resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
paths = ["/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory.current", "/sys/fs/cgroup/memory.stat",
         "/sys/fs/cgroup/memory/memory.limit_in_bytes", "/sys/fs/cgroup/memory/memory.usage_in_bytes",
         "/sys/fs/cgroup/memory/memory.stat"]
print(json.dumps({"meminfo": Path("/proc/meminfo").read_text(), "cgroup": {
    path: Path(path).read_text() for path in paths if Path(path).exists()}}, indent=2))
