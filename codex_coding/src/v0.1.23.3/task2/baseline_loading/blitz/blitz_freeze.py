"""Freeze the Blitz runner's local import closure and retained runtime entry points."""
import ast
import hashlib
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[6]
SHA = "594e91127714349c183d935ac68d7b0eb63fc1ed"
SEEDS = (
    "codex_coding/src/v0.1.23.3/task2/baseline_loading/blitz/run_blitz.py",
    "codex_coding/src/v0.1.23.3/task2/baseline_loading/blitz/blitz_freeze.py",
    "codex_coding/src/v0.1.23.3/task2/basic_scenarios/run_scenarios.py",
    "codex_coding/src/v0.1.23.3/task2/basic_scenarios/runtime_support.py",
    "codex_coding/src/serving/sglang_fluid_launch.py",
    "main_lib/fluidckpt/benchmarks/blitz_adapter.py",
    "main_lib/fluidckpt/cluster/worker.py",
)
UPSTREAM_FILES = (
    "router_v2/src/replica/steersman/mod.rs",
    "router_v2/src/replica/cybernetics/exec_blitz.rs",
    "src/service/service.cc",
    "router_v2/Cargo.toml",
)


def project_imports(path, root):
    """Follow repository module imports without importing torch or contacting workers.

    Function-local FluidCKPT imports are included because the retained backend and
    engine use lazy imports. Unused experiment scripts imported only by bootstrap
    helpers are not execution entry points of this runner and are not followed.
    """
    relative = path.relative_to(root / "main_lib") if root / "main_lib" in path.parents else None
    package = list(relative.with_suffix("").parts[:-1]) if relative else []
    if relative and path.name == "__init__.py":
        package = list(relative.parts[:-1])
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            prefix = package[:len(package)-node.level+1] if node.level else []
            if node.module:
                prefix += node.module.split(".")
            module = ".".join(prefix)
            names = [module, *(module+"."+alias.name for alias in node.names if alias.name != "*")]
        else:
            continue
        for name in names:
            if name != "fluidckpt" and not name.startswith("fluidckpt."):
                continue
            pieces = name.split(".")
            candidate = root / "main_lib" / Path(*pieces)
            file_path = candidate.with_suffix(".py")
            module_path = file_path if file_path.is_file() else candidate / "__init__.py"
            if module_path.is_file():
                yield module_path
                # Package initializers also execute when importing a submodule.
                for count in range(1, len(pieces)):
                    initializer = root / "main_lib" / Path(*pieces[:count]) / "__init__.py"
                    if initializer.is_file():
                        yield initializer


def freeze_paths(root=ROOT):
    root = Path(root).resolve()
    queue, seen = [root / name for name in SEEDS], set()
    while queue:
        path = queue.pop()
        if path in seen:
            continue
        if not path.is_file():
            raise FileNotFoundError("required Blitz dependency absent: " + str(path))
        seen.add(path)
        queue.extend(project_imports(path, root))
    for checkout in ("blitz-scale", "blitz-scale-hip"):
        repo = root / "lib_cite" / checkout
        actual = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
        if actual != SHA:
            raise ValueError("Blitz source checkout differs from frozen SHA: " + checkout)
        seen.update(repo / name for name in UPSTREAM_FILES)
    return sorted(seen)


def code_freeze(root=ROOT):
    root = Path(root).resolve()
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in freeze_paths(root)}
