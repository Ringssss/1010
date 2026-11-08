"""Freeze the first-stage protocol before reanalysis and isolated layout measurements."""
from datetime import datetime
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
out = ROOT/'codex_coding/results/v0.1.23.3/task2/paper_e1_e8/protocols'
common = {'development': 'v0.1.23.3', 'task': 'task2', 'created_at': datetime.now().astimezone().isoformat(),
          'phase': 'exploratory', 'do_not_mutate_existing_serving_session': True,
          'primary_metric': 'same-host max(weights_ready)-min(load_begin)',
          'historical_reanalysis_is_not_a_new_hardware_run': True}
protocols = {
 'e1_c01_history_r01': dict(experiment_id='e1', method='read-only historical event audit',
    sources=['motivation/serial_cost09', 'motivation/cpu_first03', 'motivation/cpu_first08'],
    question='Are recorded loading boundaries usable, and what variation is visible in same-condition observations?',
    checks=['one begin and ready per rank, matching host and monotonic order', 'correct output flags',
            'join sample, recall phase, raw event files', 'preserve failed parent status'],
    repetitions='existing two observations per serial_cost09 condition; no new inference',
    decision='qualify reuse for second-scale historical contrasts only; no tail stability or current-runtime acceptance claim'),
 'e2_c01_history_r01': dict(experiment_id='e2', method='read-only capacity-pressure reanalysis',
    sources=['motivation/verified_pressure', 'motivation/cpu_first03', 'motivation/cpu_first08'],
    question='Does local DRAM scarcity expose a large cost for local-only recovery relative to cluster DRAM?',
    variable='local DRAM fraction 0, approximately 0.5, 1; eligible source policy within each point',
    checks=['full DRAM coverage conserved in historical sample', 'same fraction within each policy pair',
            'source run and metrics retained', 'policy source traffic recorded'],
    limits=['local-only baseline deliberately cannot access remote DRAM; this does not prove joint planning beats remote-aware greedy',
            '100-percent point is from a repaired later run; do not aggregate it as a same-version sweep'],
    decision='report within-point T_ready and resource/provenance evidence; classify locality motivation, not optimizer increment'),
 'e3_c01_layouts_r01': dict(experiment_id='e3', method='isolated synthetic weight-region recovery',
    question='At the same extra HBM budget, do concentrated, distributed, replicated and hybrid layouts produce different real source use and recovery costs?',
    nodes=['node28'], physical_gpus=[2,3,4,5], target_sets={'A':[0,1], 'B':[2,3]},
    rank_bytes=(2 << 30)+17, complete_base_dram_bytes=2*((2 << 30)+17), extra_hbm_bytes=2 << 30,
    per_worker_staging_bytes=64 << 20, target_output_bytes=2*((2 << 30)+17), executor='serial', chunk_bytes=32 << 20,
    layout_order=['concentrated','distributed','replicated','hybrid'],
    scenario_order=[['A','B'],['B','A'],['A','B'],['B','A']], scenario_weights={'A':0.5,'B':0.5},
    per_configuration_repeats=1, identical_baseline_repeats=2,
    observed_metric='coordinator fixture prepare+execute+publication; SHA separately; not SGLang T_ready',
    gate='scientific performance outcome remains inconclusive if spread/gain is comparable to repeated baseline variation; no full-model gain claim',
    safety=['separate container/mesh, no calls that mutate protected worker sessions',
            'allow only known low-memory background keepers; GPU use below 4GiB, at least 12GiB free',
            'require free-only NUMA headroom for own base and initialization scratch', 'abort on failed path warmup'],
    limits=['synthetic functional execution comparison is an e3 precursor; full model primary-metric acceptance remains for e5',
            'co-resident keeper contexts and any interference are recorded; no exclusive-hardware claim'])}
out.mkdir(parents=True, exist_ok=True)
for name, protocol in protocols.items():
    path = out/(name+'.json')
    if path.exists(): raise ValueError(f'protocol already exists: {path}')
    path.write_text(json.dumps(dict(common, run_id=name, **protocol), indent=2))
    print(path.relative_to(ROOT))
