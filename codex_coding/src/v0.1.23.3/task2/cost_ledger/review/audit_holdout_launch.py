"""Targeted C2 plan audit when C1's qualified implementation is unchanged."""
import argparse
import json
from pathlib import Path

from audit_core import Audit, HOLDOUT_ORDER, CONFIGS, expected_chunks


def main():
    parser=argparse.ArgumentParser()
    for key in ('calibration-prepared','prepared','stage','output'):parser.add_argument('--'+key,type=Path,required=True)
    args=parser.parse_args();audit=Audit()
    before=audit.read(args.calibration_prepared/'launch_plan.json');after=audit.read(args.prepared/'launch_plan.json')
    audit.check('new C2 session and exact unchanged physical configuration',after['experiment_stage']=='C2'
        and after['session']!=before['session'] and after['workers']==before['workers']
        and after['ssd']==before['ssd']=={} and after['necessary_pairs']==before['necessary_pairs']==[])
    for field in ('aggregate_budget','lifetime_s','startup_s','frozen_model_ref','analysis_predeclared_ref','physical_role_mapping_ref'):
        audit.check('unchanged contract: '+field,after[field]==before[field])
    audit.check('new C2 mode/output namespace',after['inventory_mode_file']!=before['inventory_mode_file']
        and after['session'] in after['inventory_mode_file'])
    audit.check('fixed V2→V1 order and unchanged measured state/gates',after['protocol']['order']==HOLDOUT_ORDER
        and after['protocol']['state_contract']==before['protocol']['state_contract']
        and after['protocol']['gates']==before['protocol']['gates'])
    for row in after['protocol']['cases']:
        sizes=CONFIGS[row['id']]
        audit.check('holdout shape '+row['id'],row['op_mib']==sizes and row['N_mib']==sum(sizes)
            and row['K']==3 and row['expected_chunk_mib']==expected_chunks(sizes))
    oldspec=audit.read(args.calibration_prepared/'node27.setup.spec.json');newspec=audit.read(args.prepared/'node27.setup.spec.json')
    audit.check('entire source/role/port package unchanged',oldspec==newspec)
    append=audit.read(args.prepared/'append_assets.json')
    audit.check('no rewritten or appended implementation for C2',append['assets']==[])
    previous=before['processes'][0];current=after['processes'][0]
    expected_argv=list(previous['argv'])
    for flag,value in [('--session',after['session']),('--inventory-mode-file',after['inventory_mode_file'])]:
        expected_argv[expected_argv.index(flag)+1]=value
    audit.check('same worker launch argv except new session/mode',current['argv']==expected_argv)
    expected_env=dict(previous['env']);expected_env['NCCL_TOPO_DUMP_FILE']=current['env']['NCCL_TOPO_DUMP_FILE']
    audit.check('same environment with new unique dump',current['env']==expected_env
        and current['env']['NCCL_TOPO_DUMP_FILE']!=previous['env']['NCCL_TOPO_DUMP_FILE']
        and current['env']['NCCL_TOPO_DUMP_FILE']!=current['env']['NCCL_TOPO_FILE'])
    audit.check('fresh own supervisor output',current['out']!=previous['out'] and after['session'] in current['out'])
    for reference in [after['prediction_ref'],after['calibration_review_ref'],after['analysis_predeclared_ref'],after['frozen_model_ref']]:audit.reference(reference)
    frozen=audit.read(after['prediction_ref']['path']);review=audit.read(after['calibration_review_ref']['path'])
    audit.check('C1 authorization matches independent exact raw-resource-model review',frozen['frozen'] is True
        and frozen['holdout_authorized_by_analysis'] is True and review['passed'] is True
        and review['review_scope']=='C1_raw_resource_model' and review['CPU_REPLAY_ONLY'] is False
        and review['calibration_bundle_ref']==frozen['calibration_bundle_ref'])
    audit.check('two frozen forecasts precede new session; no published/live forecast',
        [row['case_id'] for row in frozen['predictions']]==HOLDOUT_ORDER and frozen['calibration_session_id']==before['session']
        and frozen['published_prediction'] is None and frozen['live_prediction'] is None)
    stage=audit.read(args.stage/'manifest.json')
    gate=audit.read(args.stage/'preflight.json')
    audit.check('stage exact prepared plan and no GPU start',stage['GPU_launches']==0 and stage['append_only'] is True
        and gate['passed'] is True and gate['GPU_launches']==0)
    fresh=audit.read(args.stage/'after_node27/stdout.json')
    audit.check('fresh selected container quiet/ports/assets; physical runtime admission still pending',
        fresh['container']['Id']==newspec['container_id'] and fresh['cgroup']['files']['pids.current']=='1'
        and all(fresh['ports_free'].values()) and all(fresh['source_assets'].get(row['relative_path'])==row['sha256'] for row in newspec['assets']))
    result=audit.save(args.output,'Targeted C2 launch contract; unchanged implementation inherits C1 CPU/source qualification; new runtime physical gate remains required',
        status='C2_source_and_frozen_contract_passed',physical_admission=False,session_id=after['session'],prediction_ref=after['prediction_ref'])
    print(json.dumps({'passed':result['passed'],'checks':len(audit.checks),'failed':[x['name'] for x in audit.checks if not x['passed']]}))
    if not result['passed']:raise SystemExit(1)


if __name__=='__main__':main()
