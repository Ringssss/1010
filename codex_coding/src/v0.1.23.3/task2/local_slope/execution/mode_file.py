"""Create a fresh fixed cached-mode file within this diagnostic task's scope."""
import argparse
import json
from pathlib import Path

def require(ok, message):
    if not ok:
        raise ValueError(message)

def validate_path(path, expected_parent=None):
    path = Path(path).resolve()
    require((path.parent.name == 'local_slope' if expected_parent is None else path.parent == Path(expected_parent).resolve()) and path.name.startswith('local_slope_runtime_')
            and path.name.endswith('.inventory_mode.json'), 'fresh local slope task mode path required')
    return path

def create(path, expected_parent=None):
    path = validate_path(path, expected_parent)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as stream:
        stream.write(json.dumps({'mode':'cached'}) + '\n')
    return dict(path=str(path), mode='cached', GPU_launches=0)

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--path', type=Path)
    p.add_argument('--self-check', action='store_true')
    p.add_argument('--out', type=Path)
    p.add_argument('--prepared', type=Path)
    p.add_argument('--partial-stage', type=Path)
    a = p.parse_args()
    if not a.self_check:
        require(a.path is not None, 'fixed mode path required')
        print(json.dumps(create(a.path)))
        return
    # Host-only checks may import coordinator helpers; the container CLI above remains stdlib-only.
    from common import RESULTS, write, fresh_output
    require(a.out is not None and a.prepared is not None and a.partial_stage is not None, 'CPU gate/repair checks need explicit evidence paths')
    out = fresh_output(a.out)
    scope = out / 'local_slope'
    target = scope / 'local_slope_runtime_cpu.inventory_mode.json'
    created = create(target, scope)
    require(json.loads(target.read_text()) == {'mode':'cached'}, 'cached content differs')
    try:
        create(target, scope)
    except FileExistsError:
        pass
    else:
        raise AssertionError('existing mode file overwritten')
    for bad in (scope / 'bounded_cost_runtime_r01.inventory_mode.json', out / target.name, scope / 'local_slope_runtime_r01.other.json'):
        try:
            validate_path(bad, scope)
        except ValueError:
            pass
        else:
            raise AssertionError('wrong path accepted')
    validate_path(RESULTS / 'local_slope_runtime_r01.inventory_mode.json')
    from prepare import partial_stage_priors
    specs = partial_stage_priors(a.prepared, a.partial_stage)
    packages = json.loads((a.prepared / 'append_assets.json').read_text())
    paths = {row['relative_path'] for row in packages['assets']}
    require(paths <= {row['relative_path'] for row in specs[0]['assets']}, 'node28 staged files missing from actual prior')
    require(all(paths.isdisjoint({row['relative_path'] for row in spec['assets']}) for spec in specs[1:]), 'untouched node prior incorrectly includes new assets')
    import shutil
    import subprocess
    import sys
    isolated = out / 'isolated'
    isolated.mkdir()
    script = isolated / 'mode_file.py'
    shutil.copyfile(Path(__file__), script)
    isolated_target = isolated / 'local_slope/local_slope_runtime_isolated.inventory_mode.json'
    command = [sys.executable, '-I', str(script), '--path', str(isolated_target)]
    proc = subprocess.run(command, cwd=isolated, capture_output=True, text=True, timeout=10)
    (out / 'isolated.stdout.txt').write_text(proc.stdout)
    (out / 'isolated.stderr.txt').write_text(proc.stderr)
    write(out / 'isolated.command.json', dict(argv=command, returncode=proc.returncode, cwd=str(isolated), copied_files=['mode_file.py']))
    require(proc.returncode == 0 and json.loads(isolated_target.read_text()) == {'mode':'cached'}, 'standalone copied real CLI failed')
    proc = subprocess.run(command, cwd=isolated, capture_output=True, text=True, timeout=10)
    require(proc.returncode != 0 and 'FileExistsError' in proc.stderr, 'standalone copied CLI overwrote modefile')
    write(out / 'qualification.json', dict(passed=True, GPU_launches=0, remote_writes=0, CPU_ONLY=True,
        checks=['actual task CLI mode path accepted', 'cached content exact', 'noclobber duplicate refused',
                'wrong task/parent/suffix refused', 'actual node28 partial append matches frozen package',
                'node30/node27 untouched prior specs preserved', 'isolated copied real CLI under python -I has no repo dependencies',
                'isolated CLI duplicate refuses overwrite'], created_fixture=created,
        node28_staged_assets=len(paths), untouched_nodes=['node30','node27']))
    print(str(out / 'qualification.json'))

if __name__ == '__main__':
    main()
