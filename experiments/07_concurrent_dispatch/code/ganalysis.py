"""Auditable G-only pairing and backend evidence; no formal analysis from pilots."""
import math,statistics,collections,json
from gcommon import *

ACTIVITY="request in actual scheduled-token set during executor forward service; completed model-runner request mapping; excludes queued/preempted states; includes dispatch/IPC overhead; not GPU kernel parallelism"
def event_evidence(events,response_ids,completed=True):
    gaps=[];by_pid=collections.defaultdict(list)
    for e in events:by_pid[e["pid"]].append(e)
    for pid,es in by_pid.items():
        seq=[e["seq"] for e in es]
        if seq and (seq[0]!=1 or any(b!=a+1 for a,b in zip(seq,seq[1:]))):gaps.append(pid)
    inversions=[{"pid":pid,"previous_seq":a["seq"],"next_seq":b["seq"],"backwards_ns":a["t_ns"]-b["t_ns"]}
                for pid,es in by_pid.items() for a,b in zip(es,es[1:]) if b["t_ns"]<a["t_ns"]]
    # Seq checks write completeness. Time/state comparisons use the common monotonic clock.
    events=sorted(events,key=lambda e:(e["t_ns"],e["pid"],e["seq"]))
    core_events=[e for e in events if e["kind"]=="scheduled_step"]
    native={rid for e in core_events for rid in e["scheduled_tokens"]}
    mapping={}
    for response_id in response_ids:
        found=[rid for rid in native if rid==response_id or rid.startswith(response_id+"-")]
        if len(found)==1:mapping[response_id]=found[0]
    targets=set(mapping.values())
    starts={e["step_id"]:e for e in events if e["kind"]=="executor_begin" and e.get("step_id")}
    ends={e["step_id"]:e for e in events if e["kind"]=="executor_end" and e.get("step_id")}
    updates={e["step_id"]:e for e in events if e["kind"]=="update_begin" and e.get("step_id")}
    done={e["step_id"] for e in events if e["kind"]=="update_done" and e.get("step_id")}
    transitions=collections.defaultdict(list)
    for e in events:
        if e["kind"]=="status_transition" and e.get("request_id") in targets:transitions[e["request_id"]].append(e)
    intervals=[];bad_steps=[];relevant_steps=[]
    for step in core_events:
        selected=targets.intersection(k for k,v in step["scheduled_tokens"].items() if v>0)
        if not selected:continue
        sid=step["step_id"];relevant_steps.append(sid)
        begin=starts.get(sid);end=ends.get(sid);update=updates.get(sid)
        running={x["id"] for x in step["snapshot"]["running"] if x["status"]=="RUNNING"}
        output=set(update.get("output_request_ids",[])) if update else set()
        if not begin or not end or not update or sid not in done or end.get("error") or not selected.issubset(running) or not selected.issubset(output):
            bad_steps.append(sid);continue
        t0=begin["t_ns"];t1=min(end["t_ns"],update["t_ns"])
        if t1<=t0:bad_steps.append(sid);continue
        prior={rid:[x for x in transitions[rid] if x["t_ns"]<=t0] for rid in selected}
        if any(not prior[rid] or prior[rid][-1]["new"]!="RUNNING" for rid in selected):
            bad_steps.append(sid);continue
        if any(x["new"]!="RUNNING" and t0<x["t_ns"]<t1 for rid in selected for x in transitions[rid]):
            bad_steps.append(sid);continue
        intervals.append({"step_id":sid,"begin_ns":t0,"end_ns":t1,"request_ids":sorted(selected),
                          "scheduled_tokens":{rid:step["scheduled_tokens"][rid] for rid in selected}})
    terminals={rid for rid,es in transitions.items() if any(str(e["new"]).startswith("FINISHED") for e in es)}
    entries={e.get("request_id") for e in events if e["kind"]=="request_added"}
    seen={r for x in intervals for r in x["request_ids"]}
    full=completed and not gaps and len(mapping)==len(response_ids) and len(targets)==len(response_ids) and targets.issubset(terminals&entries&seen) and not bad_steps and bool(relevant_steps)
    maximum=max((len(x["request_ids"]) for x in intervals),default=0)
    overlap_ns=sum(x["end_ns"]-x["begin_ns"] for x in intervals if len(x["request_ids"])>=2)
    status=("confirmed_overlap" if maximum>=2 else "confirmed_none") if full else "unknown"
    return {"backend_overlap_status":status,"evidence_level":"L3_scheduled_token_mapping_and_L2_executor_service" if full else "incomplete",
            "activity_definition":ACTIVITY,"max_simultaneously_scheduled_requests":maximum,"overlap_service_ns":overlap_ns,
            "request_mapping":mapping,"request_mapping_fraction":len(mapping)/len(response_ids) if response_ids else None,
            "completed_scheduled_steps_fraction":(len(relevant_steps)-len(bad_steps))/len(relevant_steps) if relevant_steps else None,
            "event_write_order_time_inversions":inversions,"event_sequence_gap_pids":gaps,"bad_steps":bad_steps,"all_requests_terminal":targets.issubset(terminals),
            "time_resolution_seconds":__import__("time").get_clock_info("monotonic").resolution,
            "service_intervals":intervals,"caveat":"Scheduled step service includes dispatch and IPC, not GPU kernel time; RUNNING alone never passes."}
def load_events(folder):
    events=[];integrity=[]
    for p in sorted(Path(folder).glob("events-*.jsonl")):
        raw=p.read_bytes()
        if raw and not raw.endswith(b"\n"):raise RuntimeError("Partial event line")
        events.extend(json.loads(x) for x in raw.splitlines())
        integrity.append({"path":str(p),"prefix_bytes":len(raw),"prefix_sha256":digest(raw)})
    return events,integrity
def summarize_round(rows,n):
    valid=[x for x in rows if x.get("metric_valid")]
    complete=len(rows)==n and len(valid)==n and all(x["success"] for x in rows)
    P=sum(x["P_i"] for x in valid);H=sum(x["h_i"] for x in valid);O=sum(x.get("O_i") or 0 for x in rows)
    sent=[x["client_send_monotonic"] for x in rows if x.get("client_send_monotonic") is not None]
    done=[x["client_done_monotonic"] for x in rows if x.get("client_done_monotonic") is not None]
    duration=max(done)-min(sent) if sent and done else None
    return {"complete":complete,"expected_requests":n,"sent_requests":len(sent),"completed_requests":sum(x["success"] for x in rows),"failed_requests":sum(not x["success"] for x in rows),
            "P":P if complete else None,"H":H if complete else None,"U":P-H if complete else None,"O":O,"partial_observed_P":P,"partial_observed_H":H,
            "weighted_cache_hit":H/P if complete and P else None,"costs":costs(P,H) if complete else None,
            "round_latency":duration,"completed_request_throughput":sum(x["success"] for x in rows)/duration if duration else None,
            "completed_input_throughput":P/duration if duration else None,"output_throughput":O/duration if duration else None,
            "request_latencies":[x["request_latency"] for x in rows if x.get("request_latency") is not None]}
def paired_metrics(A,D,f):
    if not A["complete"] or not D["complete"]:return None
    dp=A["P"]-D["P"];dh=A["H"]-D["H"]
    ca=A["P"]-(1-f)*A["H"];cd=D["P"]-(1-f)*D["H"]
    return {"CAF":ca/cd,"delta_P":dp,"delta_H":dh,"delta_U":dp-dh,"length_term":dp,"cache_term":-(1-f)*dh,"delta_C":ca-cd}