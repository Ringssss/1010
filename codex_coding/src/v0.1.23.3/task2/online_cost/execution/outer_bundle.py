"""Authenticate and stage only the nineteen frozen r02 path-model artifacts."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import tarfile

from prepare import ROOT, RESULTS, digest

OLD_RUN = 'online_simple_two_node_r02'
OLD_RUNTIME = RESULTS / ('execution_runtime_' + OLD_RUN)
OLD_PHASE = OLD_RUNTIME / 'phase_calibrate'
ARCHIVE = RESULTS / 'execution_host_simple_run_r02/collection_node27.tar'
ARCHIVE_SHA256 = '7731668cde9e73b46a1dbadea3fe3e52090f5d39945786e7a94f324dd57cea25'
MODEL_SHA256 = '04febed84e3cda9fd937cf8aa5d958ab530832305919861b5dbbed7e2db42b1a'
SOURCE_PLAN = RESULTS / 'execution_setup_simple_two_node_r02/launch_plan.unbound.json'
SOURCE_PLAN_SHA256 = 'a65fd6765f45e384849e905bd702a6c224190799a7c68f14e9f941a36faef402'
MODEL_NAME = 'checkpoint_path_model.json'
RAW_NAMES = [name for rank in range(6) for name in
             (f'path_warmup_{rank}_1073741824.json', f'path_cal_{rank}_805120688.json', f'path_cal_{rank}_1073741824.json')]
NAMES = [MODEL_NAME, *RAW_NAMES]
MAX_FILE_BYTES = 4 << 20
MAX_TOTAL_BYTES = 32 << 20


def authenticated_files(archive=ARCHIVE, archive_sha256=ARCHIVE_SHA256, model_sha256=MODEL_SHA256):
    archive = Path(archive)
    if archive.stat().st_size > 512 << 20:
        raise ValueError('archive byte bound exceeded')
    archive_digest = hashlib.sha256()
    with archive.open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            archive_digest.update(block)
    actual = archive_digest.hexdigest()
    if actual != archive_sha256:
        raise ValueError('frozen archive authentication failed')
    files = {}
    with tarfile.open(archive, 'r:') as tar:
        members = tar.getmembers()
        if len(members) > 20000:
            raise ValueError('archive member bound exceeded')
        for name in NAMES:
            logical = 'phase_calibrate/' + name
            found = [member for member in members if member.name == logical]
            if len(found) != 1:
                raise ValueError('exact single whitelisted archive member required: ' + logical)
            member = found[0]
            if not member.isfile() or member.islnk() or member.issym() or not 0 < member.size <= MAX_FILE_BYTES:
                raise ValueError('regular bounded whitelisted member required: ' + logical)
            if sum(map(len, files.values())) + member.size > MAX_TOTAL_BYTES:
                raise ValueError('aggregate selected-member byte bound exceeded')
            with tar.extractfile(member) as stream:
                raw = stream.read(MAX_FILE_BYTES + 1)
            if len(raw) != member.size:
                raise ValueError('archive member size changed')
            files[name] = raw
    if sum(map(len, files.values())) > MAX_TOTAL_BYTES or digest(files[MODEL_NAME]) != model_sha256:
        raise ValueError('model authentication/aggregate byte bound failed')
    model = json.loads(files[MODEL_NAME])
    if (model.get('schema') != 'simple_exact_extent_model_v1' or model.get('online_outer_s') is not None
            or model.get('calibration_sessions') != [OLD_RUN] or model.get('online_qualified') is not False):
        raise ValueError('original unqualified path checkpoint required')
    refs = model['source_refs']
    expected = {str(OLD_PHASE / name) for name in RAW_NAMES}
    if len(refs) != 18 or {ref['path'] for ref in refs} != expected:
        raise ValueError('exact eighteen original logical source references required')
    for ref in refs:
        if not re.fullmatch('[0-9a-f]{64}', ref['sha256']) or digest(files[Path(ref['path']).name]) != ref['sha256']:
            raise ValueError('raw source-reference authentication failed')
    return files


def build_bundle(out):
    out = Path(out).resolve()
    if out.parent != RESULTS or not out.name.startswith('outer_ops_source_bundle_'):
        raise ValueError('new owned outer_ops_source_bundle_* output required')
    files = authenticated_files()
    if digest(SOURCE_PLAN.read_bytes()) != SOURCE_PLAN_SHA256:
        raise ValueError('source frozen whole-plan authentication failed')
    out.mkdir(parents=True, exist_ok=False)
    entries = []
    for name, raw in files.items():
        logical = OLD_PHASE / name
        relative = logical.relative_to(ROOT)
        local = out / 'payload' / relative
        local.parent.mkdir(parents=True, exist_ok=True)
        local.open('xb').write(raw)
        entries.append(dict(logical_absolute_path=str(logical), relative_path=str(relative),
                            bundle_relative_path=str(local.relative_to(out)),
                            archive_member='phase_calibrate/' + name, sha256=digest(raw), size_bytes=len(raw)))
    plan_copy = out / 'source_launch_plan.unbound.json'
    plan_copy.open('xb').write(SOURCE_PLAN.read_bytes())
    manifest = dict(schema='outer_frozen_path_bundle_v1', archive_ref=dict(path=str(ARCHIVE), sha256=ARCHIVE_SHA256),
                    source_checkpoint_path_model_ref=dict(path=str(OLD_PHASE / MODEL_NAME), sha256=MODEL_SHA256),
                    source_frozen_plan_ref=dict(path=str(SOURCE_PLAN), sha256=SOURCE_PLAN_SHA256),
                    source_frozen_plan_bundle_relative_path=plan_copy.name, entries=entries,
                    file_count=len(entries), total_bytes=sum(e['size_bytes'] for e in entries),
                    extraction='explicit regular whitelist reads; no extract/extractall; no links or archive-selected destinations',
                    runtime_identity_reuse=False, remote_writes=0, GPU_launches=0)
    (out / 'manifest.json').open('x').write(json.dumps(manifest, indent=2) + '\n')
    return manifest


def bundle_assets(bundle):
    bundle = Path(bundle).resolve()
    manifest = json.loads((bundle / 'manifest.json').read_text())
    if (manifest.get('schema') != 'outer_frozen_path_bundle_v1' or manifest['file_count'] != 19
            or manifest['archive_ref'] != dict(path=str(ARCHIVE), sha256=ARCHIVE_SHA256)
            or manifest['source_checkpoint_path_model_ref'] != dict(path=str(OLD_PHASE / MODEL_NAME), sha256=MODEL_SHA256)):
        raise ValueError('authenticated original bundle manifest required')
    entries = manifest['entries']
    if len(entries) != 19 or {e['logical_absolute_path'] for e in entries} != {str(OLD_PHASE / name) for name in NAMES}:
        raise ValueError('exact original bundle file set required')
    assets, files = [], {}
    for entry in entries:
        relative = (OLD_PHASE / Path(entry['logical_absolute_path']).name).relative_to(ROOT)
        expected_local = Path('payload') / relative
        if entry['relative_path'] != str(relative) or entry['bundle_relative_path'] != str(expected_local):
            raise ValueError('bundle path mapping changed')
        local = bundle / expected_local
        if local.is_symlink():
            raise ValueError('bundle symlinks forbidden')
        raw = local.read_bytes()
        if len(raw) != entry['size_bytes'] or len(raw) > MAX_FILE_BYTES or digest(raw) != entry['sha256']:
            raise ValueError('bundle exact bytes changed')
        files[relative.name] = raw
        assets.append(dict(relative_path=str(relative), sha256=digest(raw), base64=base64.b64encode(raw).decode()))
    if digest(files[MODEL_NAME]) != MODEL_SHA256:
        raise ValueError('bundle model authentication failed')
    for ref in json.loads(files[MODEL_NAME])['source_refs']:
        if digest(files[Path(ref['path']).name]) != ref['sha256']:
            raise ValueError('bundle raw evidence changed')
    raw_plan = (bundle / 'source_launch_plan.unbound.json').read_bytes()
    if digest(raw_plan) != SOURCE_PLAN_SHA256:
        raise ValueError('frozen whole-plan bytes changed')
    return manifest, assets, json.loads(raw_plan)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    result = build_bundle(args.out)
    print(json.dumps(dict(status='authenticated_cpu_bundle_only', out=str(args.out.resolve()),
                          files=result['file_count'], total_bytes=result['total_bytes'], GPU_launches=0, remote_writes=0)))


if __name__ == '__main__':
    main()
