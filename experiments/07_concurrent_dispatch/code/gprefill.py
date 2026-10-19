"""Frozen independent cold-single scheduled-prefill service estimator, not kernel time."""
import math
def cold_single_reference(diagnostic,model):
    rows=diagnostic["rows"];evidence=diagnostic["evidence"]
    if len(rows)!=1 or not rows[0].get("success") or not rows[0].get("metric_valid") or rows[0].get("h_i")!=0:
        raise RuntimeError("Prefill reference requires one successful, audited zero-hit cold request")
    if evidence.get("evidence_level")!="L3_scheduled_token_mapping_and_L2_executor_service":
        raise RuntimeError("Prefill reference requires complete native service/request mapping")
    rid=rows[0]["response_id"];native=evidence["request_mapping"].get(rid)
    if not native:raise RuntimeError("Missing cold-single native request mapping")
    from ganalysis import load_events
    events,prefixes=load_events(diagnostic["trace_folder"])
    steps={e["step_id"]:e for e in events if e["kind"]=="scheduled_step"}
    segments=[]
    for interval in evidence["service_intervals"]:
        if native not in interval["request_ids"]:continue
        step=steps.get(interval["step_id"])
        if step is None:raise RuntimeError("Missing native scheduled step")
        snapshots=[r for r in step["snapshot"]["running"] if r["id"]==native]
        if len(snapshots)!=1:raise RuntimeError("Missing unique native computed/prompt snapshot")
        row=snapshots[0];computed_after=row.get("computed");prompt=row.get("prompt")
        scheduled=step["scheduled_tokens"].get(native)
        if step.get("computed_token_snapshot_phase")!="after_scheduler_reservation":
            raise RuntimeError("Unknown snapshot counter phase; no reference fallback")
        if not all(type(v) is int for v in (computed_after,prompt,scheduled)) or prompt!=rows[0]["P_i"] or scheduled<=0:
            raise RuntimeError("Unknown or invalid scheduled-prefill classification")
        computed=computed_after-scheduled
        if computed<0:
            raise RuntimeError("Unknown or invalid scheduled-prefill classification")
        prefill_tokens=min(scheduled,max(0,prompt-computed))
        if prefill_tokens:
            duration=(interval["end_ns"]-interval["begin_ns"])/1e9
            if not math.isfinite(duration) or duration<=0:raise RuntimeError("Invalid service interval")
            segments.append({"step_id":interval["step_id"],"computed_before":computed,"computed_after_reservation":computed_after,"prompt_tokens":prompt,
                             "scheduled_tokens":scheduled,"scheduled_prefill_tokens":prefill_tokens,"duration_s":duration})
    seconds=sum(s["duration_s"] for s in segments)
    if not math.isfinite(seconds) or seconds<=0:raise RuntimeError("No valid current cold-single prefill service")
    if sum(s["scheduled_prefill_tokens"] for s in segments)!=rows[0]["P_i"]:
        raise RuntimeError("Cold-single prefill chunks do not cover audited prompt token count")
    return {"model":model,"seconds":seconds,"case":diagnostic["case"],"response_id":rid,"native_request_id":native,
            "segments":segments,"trace_prefixes":prefixes,"single_sample_predeclared":True,
            "estimator":"sum executor service intervals for this cold request where computed_before = reserved_after - scheduled; computed_before < prompt; includes final one-token prefill; decode-only excluded",
            "scope":"scheduled-prefill executor service including dispatch/IPC; not GPU kernel or wall-clock request latency",
            "frozen_before_pilot":True,"historical_reference_used_for_acceptance":False}
def require_current_reference(reference,model):
    if not isinstance(reference,dict) or reference.get("model")!=model or reference.get("frozen_before_pilot") is not True:
        raise RuntimeError("Missing current-model frozen cold-single prefill reference; no pilot request")
    seconds=reference.get("seconds")
    if type(seconds) not in (int,float) or not math.isfinite(seconds) or seconds<=0:
        raise RuntimeError("Invalid current prefill reference")
    return seconds
