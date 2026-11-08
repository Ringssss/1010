"""Replay the predeclared C1 freeze before any C2 staging or process start."""
from pathlib import Path
from common import ROOT,load,native,require,check_reference
from protocol import STATE,MODE
ANALYSIS=ROOT/'codex_coding/src/v0.1.23.3/task2/cost_ledger/analysis_gpu4/analyze.py'
def predeclared_contract(reference):
    check_reference(reference)
    analysis=load('cost_ledger_predeclare_analysis',ANALYSIS)
    document=native.read(reference['path']);analysis.verify_predeclared(document)
    return document
def prediction_contract(reference,session,*,cpu_only=False,review_reference=None):
    check_reference(reference);frozen=native.read(reference['path'])
    require(frozen.get('artifact_type')=='cost-calibration-frozen-v1' and frozen.get('frozen') is True,'C2 frozen calibration required')
    require(frozen.get('holdout_authorized_by_analysis') is True,'C2 skipped because C1 gate failed')
    analysis=load('cost_ledger_gate_analysis',ANALYSIS)
    analysis.verify_predeclared(frozen['predeclared'])
    require(frozen['protocol_sha256']==analysis.canonical(analysis.protocol()),'analysis protocol changed')
    require(frozen['configuration']==dict(STATE,measurement_mode=MODE) and frozen['state_contract']==STATE,'C2 configuration/state mismatch')
    require(session!=frozen['calibration_session_id'],'C2 must use new session')
    calibration_ref=frozen.get('calibration_bundle_ref');require(calibration_ref is not None,'C1 raw bundle missing')
    check_reference(calibration_ref);bundle=native.read(calibration_ref['path'])
    require(not bundle.get('CPU_REPLAY_ONLY') or cpu_only,'CPU replay cannot authorize GPU holdout')
    require(analysis.encoded(analysis.calibrate(bundle,frozen['predeclared'],calibration_ref))==frozen,'frozen artifact differs from independently replayed C1')
    if not cpu_only:
        require(review_reference is not None,'C2 requires independent C1 raw audit')
        check_reference(review_reference);review=native.read(review_reference['path'])
        require(review.get('passed') is True and review.get('review_scope')=='C1_raw_resource_model' and not review.get('CPU_REPLAY_ONLY') and review.get('calibration_bundle_ref')==calibration_ref,'C1 independent raw/resource audit failed/mismatched')
    return frozen
