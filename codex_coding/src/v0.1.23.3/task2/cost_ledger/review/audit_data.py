"""Audit native raw hook ownership/counts and independently reconstruct C1 fits."""
import argparse
from fractions import Fraction
import json
from pathlib import Path

from audit_core import Audit, CONFIGS, CALIBRATION_ORDER, HOLDOUT_ORDER, expected_chunks, inspect_partition
from audit_numeric import reconstruct, serializable


def rational(value):
    return Fraction(value["numerator"], value["denominator"]) if isinstance(value, dict) else Fraction(value)


def worker(audit, sample):
    label = sample["case_id"]
    ledger = sample["worker_ledger"]
    primary = ledger["primary_span"]
    domain = ledger["clock_domain"]
    audit.check(label+": independently sampled complete native method duration", primary["name"]
                == "complete_native_execute_method" and primary["end_ns"]-primary["begin_ns"]
                == ledger["worker_ns"] == sample["measured"]["worker_ns"])
    parent = dict(start_ns=primary["begin_ns"], end_ns=primary["end_ns"], clock_domain=domain)
    children = [dict(start_ns=x["begin_ns"], end_ns=x["end_ns"], clock_domain=domain)
                for x in sorted(ledger["primitive_spans"], key=lambda row: row["begin_ns"])]
    containment = inspect_partition(parent, children)
    audit.check(label+": child ownership/containment independently checked", containment["passed"], containment)
    expected = expected_chunks(CONFIGS[label])
    copies = ledger["copy_submit_spans"]
    syncs = [x for x in ledger["primitive_spans"] if x["name"] == "device_sync"]
    lands = [x for x in ledger["primitive_spans"] if x["name"] == "land_submit"]
    audit.check(label+": actual successful copy and sync spans match chunk byte vector",
                [x["bytes"] for x in copies] == [x*(1 << 20) for x in expected]
                and len(copies) == len(syncs) == len(lands) == len(expected)
                and ledger["actual"] == sample["actual"])
    keys = [(x["op_id"], x["chunk_index"]) for x in copies]
    audit.check(label+": every copy has exactly one matching land and successful sync",
                len(set(keys)) == len(keys) and keys == [(x["op_id"],x["chunk_index"]) for x in syncs]
                == [(x["op_id"],x["chunk_index"]) for x in lands])
    for copy, land, sync in zip(copies, lands, syncs):
        audit.check(label+": nested copy/land ownership "+str(copy["chunk_index"]),
                    land["begin_ns"] <= copy["begin_ns"] <= copy["end_ns"] <= land["end_ns"]
                    <= sync["begin_ns"] <= sync["end_ns"])
    raw = ledger["raw_tape"]
    audit.check(label+": native op log observations retain distinct service boundary",
                len(raw["op_begins"]) == len(raw["op_ends"]) == len(CONFIGS[label])
                and [x[0] for x in raw["op_begins"]] == [x[0] for x in raw["op_ends"]]
                and ledger["native_service_ns"] == sample["measured"]["service_ns"])
    audit.check(label+": actor is actual executor HTTP handler birth", ledger["actor"] is not None
                and ledger["actor"]["clock_domain"] == domain and ledger["actor"]["born_ns"] <= primary["begin_ns"]
                and ledger["actor"]["birth"] and ledger["actor"]["generation"] >= 1)
    audit.check(label+": instrumentation setup/metadata are independently outside native method",
                ledger["setup_span"]["end_ns"] == primary["begin_ns"]
                and ledger["setup_span"]["begin_ns"] <= primary["begin_ns"]
                and primary["end_ns"] <= ledger["metadata_begin_ns"] <= ledger["metadata_end_ns"]
                <= ledger["wrapper_observed_end_ns"])
    for identity in ledger["identities"]:
        target = identity["target"]
        audit.check(label+": fresh exact native target allocation with no prewrite", target is not None
                    and target["writes"] == [] and target["n"] == sum(CONFIGS[label])*(1 << 20)
                    and target["allocate_end_ns"] <= primary["begin_ns"]
                    and identity["target_id"] == sample["target_id"])
    audit.check(label+": one completed target write generation without phase reset",
                len(ledger["target_histories_after"]) == 1
                and len(ledger["target_histories_after"][0]["writes"]) == 1)
    audit.check(label+": primary executes original inherited product method; no CUDA event domain",
                ledger["CUDA_events"] == 0 and ledger["original_copy_path"]
                == "inherited fluidckpt.cluster.worker.ClusterWorker.execute/_execute/land")
    return containment


def request(audit, raw):
    label = raw["case_id"]
    clock = raw["controller_ledger"]
    begin, end = clock["begin_ns"], clock["end_ns"]
    domain = clock["clock_domain"]
    spans = sorted(clock["rpc_spans"], key=lambda x:x["begin_ns"])
    containment = inspect_partition(dict(start_ns=begin,end_ns=end,clock_domain=domain),
        [dict(start_ns=x["begin_ns"],end_ns=x["end_ns"],clock_domain=x.get("clock_domain",domain)) for x in spans])
    audit.check(label+": controller independent RPC nesting/order", containment["passed"], containment)
    audit.check(label+": filled/published/return milestones retain same controller clock",
                begin < clock["filled_ns"] <= clock["published_ns"] <= end
                and clock["filled_ns"]-begin == raw["measured"]["full_filled_ns"]
                and clock["published_ns"]-begin == raw["measured"]["published_ns"])
    flow = raw["flow"]
    ops = flow["plan"]["ops"]
    sizes = CONFIGS[label]
    cursor = 0
    for index, (op,size) in enumerate(zip(ops,sizes)):
        audit.check(label+": logical disjoint source/target extent "+str(index),
                    op["op_id"] == index and op["n"] == size*(1 << 20)
                    and op["off"] == op["source_offset"] == cursor
                    and op["src_w"] == op["dst_w"] == 0)
        cursor += size*(1 << 20)
        actual_chunks = raw["worker_ledger"]["actual_op_chunk_bytes"]
        audit.check(label+": actual per-op chunk vector "+str(index),
                    actual_chunks.get(str(index),actual_chunks.get(index)) == [x*(1 << 20) for x in expected_chunks([size])])
    verified = flow["verification"]
    audit.check(label+": full SHA operation rows and bytes, not only passed flag",
                len(ops) == len(sizes) == len(verified["operations"])
                and verified["checked_bytes"] == cursor and all(x.get("source_sha256")
                    and x["source_sha256"] == x.get("target_sha256") and len(x["source_sha256"]) == 64
                    for x in verified["operations"].values()))
    for op in ops:
        proof = verified["operations"].get(str(op["op_id"]),verified["operations"].get(op["op_id"]))
        audit.check(label+": complete SHA coverage "+str(op["op_id"]), proof["n"] == op["n"])
    source_identity = raw["source_lineage"]["source"]
    sources = raw["worker_ledger"]["identities"][0]["sources"]
    audit.check(label+": source epoch/generation/content lineage equals exact leased plan",
                all(op["replica_id"] == source_identity["id"] and op["generation"] == source_identity["generation"]
                    and op["source_epoch"] == source_identity["epoch"] and op["identity"] == source_identity["identity"]
                    for op in ops) and all(x["replica_id"] == source_identity["id"]
                    and x["generation"] == source_identity["generation"] and x["identity"] == source_identity["identity"]
                    and x["epoch"] == source_identity["epoch"] for x in sources))
    audit.check(label+": target exact release result, not lifecycle assertion", raw["cleanup"]["event"]
                == "end_"+label and raw["cleanup"]["objects"]
                == [{"id":raw["target_id"],"status":"released"}])
    gate = raw["gate"]["node27"]
    audit.check(label+": runtime/staging-aware actual request resource gate",
                gate["dram_current_bytes"]+(256 << 20) <= gate["dram_limit_bytes"] == 6 << 30
                and gate["hbm_absolute_upper_bytes"]+gate["planned_incremental_upper_bytes"]+(256 << 20)
                    <= gate["hbm_limit_bytes"] == 2 << 30 and gate["require_idle"] is True
                and not gate["transactions"] and gate["planned_incremental_upper_bytes"] == cursor)
    prereg = audit.read(raw["preregistration_ref"]["path"])
    audit.reference(raw["preregistration_ref"])
    audit.check(label+": plan frozen before request online inventory", prereg["saved_ns"] < begin
                and prereg["measurements"] == 0 and prereg["plans"][label] == flow["plan"])
    source = raw["source_lineage"]
    audit.reference(source["bootstrap_ref"])
    bootstrap = audit.read(source["bootstrap_ref"]["path"])
    audit.check(label+": full96MiB source bootstrap accounted separately", source["source_bytes"] == 96 << 20
                and bootstrap["fit_sample"] is False and bootstrap["flow"]["verification"]["checked_bytes"] == 96 << 20
                and next(iter(bootstrap["flow"]["verification"]["operations"].values()))["target_sha256"]
                == source["source_sha256"])
    clocks = raw["worker_ledger"]["raw_tape"]["native_clock_samples"]
    audit.check(label+": unchanged native float call layout retained with raw companions", len(clocks) == 2+3*len(sizes))
    for index, record in enumerate(flow["execution"][0]["records"]):
        represented = record["elapsed_s"]*1e9
        original = (clocks[2+3*index][0]-clocks[1+3*index][0])*1e9
        audit.check(label+": native float representation "+str(index), abs(represented-original) <= 1000)
        service = raw["worker_ledger"]["service_spans"][index]
        logs = [x for x in raw["worker_ledger"]["primitive_spans"] if x["op_id"] == index]
        begin_log = next(x for x in logs if x["name"] == "begin_log")
        end_log = next(x for x in logs if x["name"] == "end_log")
        audit.check(label+": service includes beginlog and excludes endlog "+str(index),
                    service["begin_ns"] <= begin_log["begin_ns"] < begin_log["end_ns"] <= service["end_ns"]
                    <= end_log["begin_ns"] < end_log["end_ns"])
    return containment


def holdout(audit,bundle,raw_rows,frozen,validation):
    ref=frozen["calibration_bundle_ref"];audit.reference(ref);training=audit.read(ref["path"])
    math=reconstruct(training["samples"],training["observer"])
    audit.check("holdout uses fresh session with identical configuration/state",
        bundle["session_id"]!=training["session_id"] and bundle["configuration"]==training["configuration"]
        and bundle["state_contract"]==training["state_contract"] and not bundle.get("CPU_REPLAY_ONLY"))
    oldraw=audit.read(training["samples"][0]["raw_refs"][0]["path"])
    audit.check("new worker incarnation/source lineage, same declared physical role",
        all(x["source_lineage"]["source"]["epoch"]!=oldraw["source_lineage"]["source"]["epoch"]
            and x["source_lineage"]["source"]["gpu"]==oldraw["source_lineage"]["source"]["gpu"]
            and x["source_lineage"]["source"]["numa"]==oldraw["source_lineage"]["source"]["numa"] for x in raw_rows))
    audit.check("new whole96MiB bootstrap retains source content SHA across sessions",
        all(x["source_lineage"]["source_sha256"]==oldraw["source_lineage"]["source_sha256"] for x in raw_rows))
    for raw in raw_rows:
        prereg=audit.read(raw["preregistration_ref"]["path"])
        audit.reference(prereg["prediction_ref"])
        audit.check(raw["case_id"]+": unchanged model reference saved before any holdout request",
            prereg["prediction_ref"]==validation["frozen_ref"] and raw["prediction"]
                ==next(x for x in frozen["predictions"] if x["case_id"]==raw["case_id"]))
    layers=("worker_ns","controller_execute_ns","full_filled_ns")
    limits={"worker_ns":250000,"service_ns":50000,"controller_execute_ns":1000000,"full_filled_ns":2000000}
    scores={"worker":{},"service":{}}
    for kind,boundary,relevant in (("worker","worker_ns",layers),("service","service_ns",("service_ns",))):
        for name,candidate in math[boundary]["candidates"].items():
            errors={};all_pass=True
            claimed=validation[kind+"_candidates"][name]
            audit.check(kind+"/"+name+": original admission retained",claimed["admissible"]==candidate["admissible"])
            for sample in bundle["samples"]:
                case=sample["case_id"];base=candidate["predictions"][case]
                predictions={boundary:base,"controller_execute_ns":base+math["controller_residual_ns"],
                    "full_filled_ns":base+math["controller_residual_ns"]+math["filled_residual_ns"]}
                errors[case]={}
                for layer in relevant:
                    actual=sample["measured"][layer];pred=predictions[layer];signed=pred-actual
                    absolute=abs(signed);relative=absolute/actual;passed=absolute<=limits[layer] and relative<=Fraction(1,5)
                    errors[case][layer]=absolute;all_pass=all_pass and passed
                    other=claimed["errors"][case][layer]
                    audit.check(case+"/"+kind+"/"+name+"/"+layer+": independent frozen-only absolute/relative gate",
                        rational(other["predicted_ns"])==pred and rational(other["signed_error_ns"])==signed
                        and rational(other["absolute_error_ns"])==absolute and rational(other["relative_error"])==relative
                        and other["passed"]==passed)
            audit.check(kind+"/"+name+": all point gate retained",claimed["all_points_pass"]==all_pass)
            scores[kind][name]={"admissible":candidate["admissible"],"all_pass":all_pass,"errors":errors}
    scales={layer:max(Fraction(5000),Fraction(training["observer"]["clock_extra_p95_ns"]),
        Fraction(training["observer"]["dummy_log_scale_ns"]),abs(training["samples"][-1]["measured"][layer]
            -training["samples"][0]["measured"][layer])) for layer in ("service_ns",)+layers}
    selections={}
    for kind,relevant in (("worker",layers),("service",("service_ns",))):
        candidates=scores[kind];baseline=next((k for k,x in candidates.items() if x["admissible"]),None)
        selected=baseline if baseline and candidates[baseline]["all_pass"] else None
        for name,item in candidates.items():
            if name==baseline or not item["admissible"] or not item["all_pass"]:continue
            better=all(candidates[baseline]["errors"][case][layer]-item["errors"][case][layer]>scales[layer]
                for case in HOLDOUT_ORDER for layer in relevant)
            if better and (selected is None or all(candidates[selected]["errors"][case][layer]-item["errors"][case][layer]>scales[layer]
                for case in HOLDOUT_ORDER for layer in relevant)):selected=name
        selections[kind]=selected
        audit.check(kind+": independent conservative complexity selection",
            validation["selected_worker_and_outer" if kind=="worker" else "selected_service"]==selected)
    audit.check("full model qualification distinct from decision/physical/live claims",
        validation["complete_filled_model_qualified"]==(selections["worker"] is not None)
        and validation["decision_ready"] is False and validation["future_error_bound"] is False
        and validation["published_prediction"] is None and validation["live_prediction"] is None)
    return {"calibration_reconstruction":math,"holdout_error_absolute_ns":scores,"independent_selection":selections}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--frozen", type=Path)
    parser.add_argument("--resource-review", type=Path)
    parser.add_argument("--validation", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    audit = Audit()
    bundle = audit.read(args.bundle)
    samples = bundle["samples"]
    sequence = [x["case_id"] for x in samples]
    audit.check("exact registered phase order", sequence in (CALIBRATION_ORDER, HOLDOUT_ORDER))
    audit.check("session identity consistent", all(x["session_id"] == bundle["session_id"] for x in samples))
    audit.check("request and fresh target identities unique", len({x["request_id"] for x in samples})
                == len({x["target_id"] for x in samples}) == len(samples))
    gaps = {};raw_rows=[]
    for sample in samples:
        for item in sample["raw_refs"]:
            audit.reference(item)
        raw = audit.read(sample["raw_refs"][0]["path"])
        audit.check(sample["case_id"]+": normalized values agree with independent raw artifact",
                    all(raw[key] == sample[key] for key in ("case_id","session_id","request_id","target_id","actual","measured","flags")))
        gaps[sample["case_id"]] = worker(audit, raw)
        request(audit,raw);raw_rows.append(raw)
    audit.check("same immutable source epoch/generation/identity across all requests",
                len({json.dumps(x["source_lineage"],sort_keys=True) for x in raw_rows}) == 1)
    if bundle.get("CPU_REPLAY_ONLY"):
        trace=audit.read(args.bundle.parent.parent/"primitive_trace.json")
        for row in raw_rows:
            parent=row["worker_ledger"]["primary_span"];tid=row["worker_ledger"]["actor"]["native_tid"]
            copies=[x for x in trace if x["event"]=="copy" and x["native_tid"]==tid
                and parent["begin_ns"]<=x["begin_ns"]<=x["end_ns"]<=parent["end_ns"]]
            syncs=[x for x in trace if x["event"]=="sync" and x["native_tid"]==tid
                and parent["begin_ns"]<=x["ns"]<=parent["end_ns"]]
            chunks=[x*(1 << 20) for x in expected_chunks(CONFIGS[row["case_id"]])]
            offsets=[];cursor=0
            for size in chunks:offsets.append(cursor);cursor+=size
            audit.check(row["case_id"]+": independent CPU primitive copy byte/offset trace",
                [x["n"] for x in copies]==chunks and [x["source_offset"] for x in copies]==offsets
                and [x["target_offset"] for x in copies]==offsets and all(x["non_blocking"] is True for x in copies))
            audit.check(row["case_id"]+": independent CPU primitive sync count",len(syncs)==len(chunks))
    audit.reference(bundle["cleanup_ref"])
    final = audit.read(bundle["cleanup_ref"]["path"])
    audit.check("final catalog/used bytes/transactions empty; no SSD", final["replicas"] == [] and final["ssd"] == {}
                and all(not row["transactions"] and not any(row["used_bytes"].values()) for row in final["workers"]))
    journal = audit.read(args.bundle.parent/"journal.json")
    audit.check("exact phase request count and no failure", journal["status"] == "complete"
                and not journal.get("failure") and not journal.get("cleanup_failure")
                and journal["simulated_requests" if bundle.get("CPU_REPLAY_ONLY") else "GPU_measurements"] == len(sequence))
    reconstruction = None
    if sequence == CALIBRATION_ORDER:
        reconstruction = reconstruct(samples, bundle["observer"])
    if args.frozen and reconstruction:
        frozen = audit.read(args.frozen)
        for boundary in ("worker_ns", "service_ns"):
            independent = reconstruction[boundary]
            claimed = frozen["layers"][boundary]
            audit.check(boundary+": independent drift and observation scale", rational(claimed["E_ns"])
                        == independent["E_ns"] and claimed["drift_pass"] == independent["drift_passed"])
            for name, candidate in independent["candidates"].items():
                other = claimed["candidates"][name]
                audit.check(boundary+"/"+name+": signed raw coefficients; no clamping", list(map(rational,
                    other["raw_coefficients"])) == candidate["raw_coefficients"])
                audit.check(boundary+"/"+name+": prereg admission and exact residuals",
                    other["admissible"] == candidate["admissible"] and list(map(rational,
                    other["training_residual_ns"].values())) == candidate["raw_residuals"])
                audit.check(boundary+"/"+name+": both frozen unseen predictions independently reconstructed",
                    all(rational(other["predictions"][case]) == value for case,value in candidate["predictions"].items()))
        for field in ("controller_residual_ns", "filled_residual_ns"):
            audit.check(field+": independently first-five-only median once",
                        rational(frozen["outer"][field]) == reconstruction[field])
        audit.check("published/live remain unknown", frozen["published_prediction"] is None
                    and frozen["live_prediction"] is None)
        admitted = all(reconstruction[k]["drift_passed"] for k in ("worker_ns", "service_ns"))
        admitted = admitted and any(x["admissible"] for x in reconstruction["worker_ns"]["candidates"].values())
        audit.check("independent overall C1 authorization decision", frozen["holdout_authorized_by_analysis"] == admitted)
    if args.frozen and sequence==HOLDOUT_ORDER:
        if args.validation is None:raise ValueError("C2 requires independent validation artifact for comparison")
        frozen=audit.read(args.frozen);validation=audit.read(args.validation)
        reconstruction=holdout(audit,bundle,raw_rows,frozen,validation)
    resource_complete=False
    if args.resource_review:
        resource=audit.read(args.resource_review)
        resource_complete=audit.check("independent physical resource/cleanup audit matches exact bundle/session",
            resource.get("passed") is True and resource.get("actual_session")==bundle["session_id"]
            and resource.get("calibration_bundle_ref")==audit.refs[0] and not bundle.get("CPU_REPLAY_ONLY"))
    result = audit.save(args.output, "Independent raw native method ownership/count/lineage, complete request/SHA/release, C1 algebra and separately referenced physical resource evidence",
                        calibration_bundle_ref=audit.refs[0], review_scope=("C1_raw_resource_model" if sequence==CALIBRATION_ORDER else "C2_raw_resource_validation")
                            if resource_complete and args.frozen else "raw_request_model",
                        CPU_REPLAY_ONLY=bundle.get("CPU_REPLAY_ONLY",False), continuous_peak_unknown=True)
    (args.output/"independent_reconstruction.json").write_text(json.dumps(serializable(reconstruction), indent=2)+"\n")
    print(json.dumps({"passed": result["passed"], "checks": len(audit.checks),
                      "failed": [x["name"] for x in audit.checks if not x["passed"]]}))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
