"""CPU-only child used to verify exec-level environment inheritance."""
import json
import os
import sys

import rank_entry

if __name__ == "__main__":
    print(json.dumps({"environment": rank_entry.environment(), "torch_imported": "torch" in sys.modules,
                      "pid": os.getpid()}))
