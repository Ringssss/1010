"""Task-local imports of the immutable corrected session infrastructure."""
import importlib.util
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[6]
HERE = Path(__file__).resolve().parent
RESULTS = ROOT / 'codex_coding/results/v0.1.23.3/task2/first_op_validation'
LOCAL = ROOT / 'codex_coding/src/v0.1.23.3/task2/local_slope/execution'
OLD_RESULTS = ROOT / 'codex_coding/results/v0.1.23.3/task2/local_slope'
CALIBRATION = ROOT / 'codex_coding/results/v0.1.23.3/task2/cost_calibration'
PREVIOUS = OLD_RESULTS / 'execution_repeat_prepared_r01'
sys.path.insert(0, str(ROOT / 'main_lib'))
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
prior = load('firstop_immutable_local_common', LOCAL / 'common.py')
native, parent, ops, RUNTIME = prior.native, prior.parent, prior.ops, prior.RUNTIME
write, ref, require = prior.write, prior.ref, prior.require
budget_snapshot, check_reference, quiet_readback = prior.budget_snapshot, prior.check_reference, prior.quiet_readback

def fresh_output(path):
    path = Path(path).resolve()
    require(path.parent == RESULTS and path.name.startswith('execution_'), 'fresh first-operation output required')
    path.mkdir(parents=True, exist_ok=False)
    return path
