"""Report execution-level layout evidence separately from full model loading claims."""
import argparse
import json
from pathlib import Path
import statistics


def main(out):
    data=json.loads((out/'summary.json').read_text())
    assert data['status']=='passed' and not data['cleanup_errors'] and not data['source_hash_mismatches']
    assert data['protected_before']==data['protected_after']
    baseline=[r['data_fill_s'] for r in data['baseline']]
    spread=max(baseline)-min(baseline)
    rows=[]
    for name,layout in data['layouts'].items():
        cases=[r for r in data['observations'] if r['layout']==name]
        assert len(cases)==2
        values={r['scenario']:r['data_fill_s'] for r in cases}
        rows.append({'layout':name,'data_fill_s':values,'equal_weight_mean_s':statistics.mean(values.values()),
            'worst_s':max(values.values()),'steady_bytes':layout['steady_bytes'],
            'unique_cache_bytes':layout['unique_cache_bytes'],'build_s':layout['build_s'],
            'source_bytes':{r['scenario']:r['flow']['source_bytes'] for r in cases}})
    gap=max(r['equal_weight_mean_s'] for r in rows)-min(r['equal_weight_mean_s'] for r in rows)
    result={'experiment_id':'e3','execution_status':'completed','functional_status':'pass',
        'scientific_status':'execution_only_exploratory','rows':rows,'baseline_data_fill_s':baseline,
        'baseline_range_s':spread,'layout_mean_range_s':gap,
        'signal_larger_than_baseline_range':gap>spread,'full_model_primary_metric_validated':False,
        'limitations':['one host and synthetic contiguous targets; not SGLang weights_ready',
            'one sample per layout/target; baseline range is a diagnostic, not a confidence interval',
            'fixed layout order and co-resident protected keepers; no statistical ranking claim',
            'CPU complete bases charged equally; build costs and verification outside data-fill shown separately']}
    (out/'analysis.json').write_text(json.dumps(result,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k!='rows'},indent=2))
    for r in rows: print(json.dumps({k:v for k,v in r.items() if k!='source_bytes'}))


if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--run-dir',type=Path,required=True)
    main(p.parse_args().run_dir)
