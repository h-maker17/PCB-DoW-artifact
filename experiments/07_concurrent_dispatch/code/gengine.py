import asyncio,os,uuid,time,json,re,sys,inspect,statistics
import httpx
from transformers import AutoTokenizer
from gcommon import *
from ganalysis import *
from gchecks import require_disk,FROZEN_PREFILL_REFERENCE
from gprefill import cold_single_reference,require_current_reference
from gslots import SlotClients
from gburst import dispatch_capture,capture_http,burst_quality

def parse_metrics(text):
    out={}
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():continue
        parts=line.split();name=parts[0].split("{")[0]
        if name in ["vllm:num_requests_running","vllm:num_requests_waiting","vllm:kv_cache_usage_perc","vllm:gpu_cache_usage_perc"]:
            out[name]=out.get(name,0)+float(parts[-1])
    return out

class Engine:
    async def drain(self):
        last=None
        for _ in range(120):
            response=await self.client.get("/metrics");response.raise_for_status();last=parse_metrics(response.text)
            if "vllm:num_requests_running" not in last or "vllm:num_requests_waiting" not in last:
                raise RuntimeError("Missing drain metrics; cannot guess zero")
            if last["vllm:num_requests_running"]==0 and last["vllm:num_requests_waiting"]==0:return last
            await asyncio.sleep(.5)
        raise RuntimeError("Server still executing; no next round")

    async def reset(self,dest):
        before=await self.drain();start=time.monotonic_ns()
        response=await self.client.post("/reset_prefix_cache");response.raise_for_status()
        for _ in range(20):
            events,integrity=load_events(self.trace)
            latest=[e for e in events if e["kind"]=="cache_reset" and e["t_ns"]>=start]
            if latest:
                ev=latest[-1]
                if not ev["success"]:raise RuntimeError("Engine refused prefix reset")
                after=await self.drain()
                result={"before":before,"after":after,"engine_reset":ev,"trace_prefixes":integrity,"new_namespace":True}
                save(dest/"isolation.json",result)
                return result
            await asyncio.sleep(.05)
        raise RuntimeError("Reset HTTP200 without native reset evidence")

    async def pool_warm(self):
        await self.slots.prepare(20)

    def offline_ids(self,msg):
        return self.tokenizer.apply_chat_template(msg,tokenize=True,add_generation_prompt=True,**self.kwargs)

    def body(self,msg,salt,max_tokens=16):
        return {"model":self.model,"messages":msg,"temperature":0,"seed":42,"top_p":1,"max_tokens":max_tokens,"stream":False,"n":1,
                "cache_salt":salt,"return_token_ids":True,"chat_template_kwargs":self.kwargs}

    async def capture(self,body,dest,replica,header_id,expected_ids=None,built_request=None,client=None):
        if client is None:
            await self.slots.prepare(replica)
            client=self.slots.clients[replica-1]
        if built_request is None:
            built_request=client.build_request("POST","/v1/chat/completions",json=body,headers={"X-Request-Id":header_id})
        result=await capture_http(client,built_request)
        self.request_count+=1
        if self.active_info is not None:self.capture_sends.append(result["times"].get("send"))
        return dict(result,body=body,dest=dest,replica=replica,header_id=header_id,expected_ids=expected_ids)

    def record_capture(self,capture):
        body,dest,replica,header_id,expected_ids=[capture[k] for k in ["body","dest","replica","header_id","expected_ids"]]
        req_path=dest/f"{replica:04d}.request.json"
        times=capture["times"];status=capture["status"];error=capture["error"];invoked=capture["invoked"]
        if capture["raw_bytes"] is not None and not capture.get("raw_bytes_persisted"):
            with (dest/f"{replica:04d}.response-body.bin").open("xb") as f:f.write(capture["raw_bytes"])
            capture["raw_bytes_persisted"]=True
        parse_start=time.monotonic();raw=None
        if capture["raw_bytes"] is not None:
            try:raw=json.loads(capture["raw_bytes"])
            except Exception:raw={"unparsed_response":capture["raw_bytes"].decode(errors="replace")[:2000]}
        parse_done=time.monotonic();persist_start=time.monotonic()
        if raw is not None:save(dest/f"{replica:04d}.response.json",raw)
        usage=raw.get("usage",{}) if isinstance(raw,dict) else {}
        ids=raw.get("prompt_token_ids") if isinstance(raw,dict) else None
        P=usage.get("prompt_tokens");h=(usage.get("prompt_tokens_details") or {}).get("cached_tokens");O=usage.get("completion_tokens")
        metric_valid=(type(P)is int and type(h)is int and 0<=h<=P and isinstance(ids,list) and len(ids)==P and (expected_ids is None or ids==expected_ids))
        success=error is None and isinstance(raw,dict) and "choices" in raw
        response_id=raw.get("id") if isinstance(raw,dict) else None
        row={"request_id":header_id,"response_id":response_id or "chatcmpl-"+header_id,"attempt_id":dest.name,"replica_id":replica,
             "success":success,"HTTP_status":status,"error_type":error,"metric_valid":metric_valid,"P_i":P,"h_i":h,"U_i":P-h if metric_valid else None,"O_i":O,
             "usage_raw":usage,"prompt_token_ids_sha256":digest(json.dumps(ids)) if ids is not None else None,
             "native_ids_exact_offline":ids==expected_ids if expected_ids is not None else None,
             "raw_response_body_path":str(dest/f"{replica:04d}.response-body.bin") if capture["raw_bytes"] is not None else None,
             "transport_trace_events":capture["events"],"transport_returned_monotonic":capture["returned"],
             "response_parse_started_monotonic":parse_start,"response_parse_done_monotonic":parse_done,
             "artifact_persist_started_monotonic":persist_start,
             "client_invoke_monotonic":invoked,"client_send_monotonic":times.get("send"),"client_done_monotonic":times.get("done"),
             "request_latency":times["done"]-times["send"] if "send" in times and "done" in times else None,
             "client_first_content_token":None,"client_last_token":None,"TTFT":None,"TTFT_missing_reason":"non-stream core path",
             "finish_reason":raw["choices"][0].get("finish_reason") if success else None,
             "message_hash":digest(json.dumps(body["messages"],ensure_ascii=False)),"cache_namespace_hash":digest(body["cache_salt"]),
             "raw_request_path":str(req_path),"raw_response_path":str(dest/f"{replica:04d}.response.json") if raw is not None else None}
        row.update(experiment_id=self.experiment_id,phase="formal" if self.active_info else "calibration",server_instance_id=self.run_id)
        if self.active_info:row.update({k:self.active_info[k] for k in ("document_id","repeat_id","N","schedule","arm","source_revision","logical_id")})
        save(dest/f"{replica:04d}.request-record.json",row);append(self.folder/"request_log.jsonl",row)
        if ids is not None:save(dest/f"{replica:04d}.prompt-token-ids.json",ids)
        append(dest/"artifact-processing.jsonl",{"replica":replica,"parse_start":parse_start,"parse_done":parse_done,
               "persist_start":persist_start,"persist_done":time.monotonic(),"all_network_requests_finished":True})
        return row

    async def send(self,body,dest,replica,header_id,expected_ids=None,built_request=None):
        return self.record_capture(await self.capture(body,dest,replica,header_id,expected_ids,built_request))

    async def dispatch_prebuilt(self,schedule,bodies,dest,headers,expected,built):
        self.guard()
        clients=self.slots.ensure(len(bodies))
        async def sender(i):return await self.capture(bodies[i],dest,i+1,headers[i],expected[i],built[i],client=clients[i])
        async def prepare(indices):
            started=time.monotonic()
            result=await self.slots.prepare(len(indices))
            last_health=max(e["t"] for slot in result["slot_preparations"] for e in slot["health_trace"]
                            if e["name"]=="http11.receive_response_body.complete")
            result.update(preparation_started_monotonic=started,preparation_complete_monotonic=time.monotonic(),
                          last_health_complete_monotonic=last_health,preparation_excluded_from_core_round_latency=True)
            result["preparation_duration_s"]=result["preparation_complete_monotonic"]-started
            result["post_health_preparation_s"]=result["preparation_complete_monotonic"]-last_health
            return result
        captures,groups=await dispatch_capture(schedule,len(bodies),sender,prepare)
        # No response JSON parsing, hashing or filesystem writes until ALL captures returned.
        for c in captures:
            if c["raw_bytes"] is not None:
                with (dest/f"{c['replica']:04d}.response-body.bin").open("xb") as f:f.write(c["raw_bytes"])
                c["raw_bytes_persisted"]=True
        rows=[self.record_capture(c) for c in captures]
        save(dest/"client-burst-preparation.json",{"groups":groups,"all_transport_complete_before_processing":True,
             "pool_queue_duration":None,"pool_queue_missing_reason":"Boundary snapshots and connection traces are not per-request pool wait measurement"})
        return rows

    async def diagnostic(self,name,messages_list,salt=None,schedule="S",max_tokens=16,reset=True):
        dest=self.folder/"calibration"/name;dest.mkdir(parents=True);salt=salt or uuid.uuid4().hex
        require_disk(ROOT)
        if reset:await self.reset(dest)
        else:save(dest/"isolation-context.json",{"physical_reset_performed":False,"purpose":"unreset salt independence control"})
        bodies=[self.body(m,salt,max_tokens) for m in messages_list]
        expected=[self.offline_ids(m) for m in messages_list]
        for i,b in enumerate(bodies):
            save(dest/f"{i+1:04d}.planned.json",{"body":b,"expected_ids":expected[i]})
            save(dest/f"{i+1:04d}.request.json",b)
            (dest/f"{i+1:04d}.rendered-prompt.txt").write_text(self.tokenizer.apply_chat_template(b["messages"],tokenize=False,add_generation_prompt=True,**self.kwargs))
        headers=[f"G-{self.run_id}-cal-{name}-{i+1}" for i in range(len(bodies))]
        built=[self.client.build_request("POST","/v1/chat/completions",json=b,headers={"X-Request-Id":h}) for b,h in zip(bodies,headers)]
        rows=await self.dispatch_prebuilt(schedule,bodies,dest,headers,expected,built);await self.drain()
        events,integrity=load_events(self.trace);evidence=event_evidence(events,[r["response_id"] for r in rows],all(r["success"] for r in rows))
        out={"case":name,"trace_folder":str(self.trace),"rows":rows,"evidence":evidence,"trace_prefixes":integrity,"cache_salt":salt}
        save(dest/"result.json",out);self.calibration.append(out)
        return out

    async def salt_controls(self,base):
        warm=await self.diagnostic("cold-and-warm",[base,base])
        assert all(r["metric_valid"] and r["success"] for r in warm["rows"])
        assert warm["rows"][0]["h_i"]==0 and warm["rows"][1]["h_i"]>0
        cross=await self.diagnostic("cross-domain-unreset-B",[base],salt=uuid.uuid4().hex,reset=False)
        same=await self.diagnostic("same-domain-A-after-B",[base],salt=warm["cache_salt"],reset=False)
        assert same["rows"][0]["success"] and same["rows"][0]["metric_valid"] and same["rows"][0]["h_i"]>0
        assert cross["rows"][0]["success"] and cross["rows"][0]["metric_valid"] and cross["rows"][0]["h_i"]==0
        return warm,cross,same

    async def calibrate(self):
        await self.pool_warm()
        d=self.calibration_doc;base=messages(self.inputs["S"],self.inputs["markers"][0]+"\n"+d["G"],1)
        warm,cross,same=await self.salt_controls(base)
        fork_payloads=[]
        # Tokenization-only indexed search targets prefix blocks 16*k +/-1; no outcome search.
        orig=self.offline_ids(base);content=base[1]["content"]
        for target in [80,81,95,96,97]:
            best=None
            for pos in range(10,min(len(content),600)):
                changed=[dict(base[0]),dict(base[1])];changed[1]["content"]=content[:pos]+"X"+content[pos+1:]
                ids=self.offline_ids(changed);lc=lcp(orig,ids)
                candidate=(abs(lc-target),pos,changed,lc)
                if best is None or candidate[:2]<best[:2]:best=candidate
                if lc==target:break
            fork_payloads.append((target,best[2],best[3]))
        fork_results=[]
        for target,changed,lc in fork_payloads:
            r=await self.diagnostic("fork-"+str(target),[base,changed])
            assert all(x["metric_valid"] and x["success"] for x in r["rows"])
            expected=16*(min(lc,len(self.offline_ids(changed))-1)//16)
            assert r["rows"][1]["h_i"]==expected
            fork_results.append({"target_lcp":target,"actual_lcp":lc,"expected_hit":expected,"observed":r["rows"][1]["h_i"]})
        burst=await self.diagnostic("cold-identical-burst",[base]*5,schedule="C")
        assert all(r["metric_valid"] and r["success"] for r in burst["rows"])
        assert burst["evidence"]["backend_overlap_status"]!="unknown"
        # Capability diagnostic remains separate; does not alter core short-output policy.
        capability=burst
        if burst["evidence"]["max_simultaneously_scheduled_requests"]<2:
            raise RuntimeError("Formal startup capability unavailable; no automatic long diagnostic")
        assert capability["evidence"]["backend_overlap_status"]=="confirmed_overlap"
        assert capability["evidence"]["max_simultaneously_scheduled_requests"]>=2
        cold_times=[r["request_latency"] for c in self.calibration if c["case"] in ["cross-domain-unreset-B"] for r in c["rows"]]
        self.prefill_reference=cold_single_reference(cross,self.label)
        save(self.folder/"prefill-reference.json",self.prefill_reference)
        save(self.folder/"calibration-summary.json",{"passed":True,"coverage_claim":"individual evidenced cases, optional stream omitted",
             "salt_independence_unreset_ABA":True,"same_domain_A_after_B_hits":same["rows"][0]["h_i"],
             "legacy_burst_prefill_reference_seconds":FROZEN_PREFILL_REFERENCE[self.label],
             "full_instrumentation_overhead_measured":False,"rows":len([r for c in self.calibration for r in c["rows"]]),
             "warm_cold_hits":[r["h_i"] for r in warm["rows"]],"fork_boundaries":fork_results,"same_prompt_cold_burst_hits":[r["h_i"] for r in burst["rows"]],
             "capability":capability["evidence"],"cold_request_latency":statistics.median(cold_times),"current_model_prefill_reference":self.prefill_reference,"cold_single_prefill_service_seconds":self.prefill_reference["seconds"],"relative_rule_fraction":0.25,"absolute_rule_seconds":0.05,"legacy_tokens_gt1_prefill_proxy_seconds_not_acceptance":sum((x["end_ns"]-x["begin_ns"])/1e9 for x in cross["evidence"]["service_intervals"] if max(x["scheduled_tokens"].values())>1),"round_reset_evidence_required":True,
             "long_diagnostic_not_core":capability["case"]!="cold-identical-burst"})
        print("CALIBRATION_PASSED",self.label,"requests",self.request_count,flush=True)

    async def _round_attempt(self,doc,n,schedule,arm,position,repeat_id,info):
        rid=info["attempt_id"]
        dest=self.round_folder/"rounds"/rid;dest.mkdir(parents=True,exist_ok=True)
        isolation=await self.reset(dest);salt=uuid.uuid4().hex
        msg,calls=prepare_arm(doc,self.inputs["S"],self.inputs["markers"],n,arm)
        ids=[self.offline_ids(m) for m in msg]
        assert len(calls)==(1 if arm=="B" else n)
        for call in calls:append(dest/"tool_calls.jsonl",dict(call,tool_source_sha256=digest(inspect.getsource(SharedDocumentTool))))
        bodies=[self.body(m,salt) for m in msg]
        for i,b in enumerate(bodies):
            save(dest/f"{i+1:04d}.planned.json",{"body":b,"expected_ids":ids[i]})
            save(dest/f"{i+1:04d}.request.json",b)
            (dest/f"{i+1:04d}.rendered-prompt.txt").write_text(self.tokenizer.apply_chat_template(b["messages"],tokenize=False,add_generation_prompt=True,**self.kwargs))
        save(dest/"manifest.json",{"schema_version":"G-round-v2","model":self.label,"round_id":rid,"document_id":doc["id"],"N":n,"schedule":schedule,"arm":arm,"phase":"formal","repeat_id":repeat_id,"attempt_index":info["attempt_index"],"attempt_id":rid,"retry_of":info["retry_of"],"retry_reason":info["retry_reason"],"approved_revision":info["approved_revision"],"source_revision":info["source_revision"],
             "run_position":position,"expected_requests":n,"tool_calls":len(calls),"cache_salt":salt,"started_utc":now(),"server_run_id":self.run_id})
        headers=[f"G-{self.run_id}-{rid}-{i+1}" for i in range(n)]
        built=[self.client.build_request("POST","/v1/chat/completions",json=b,headers={"X-Request-Id":h}) for b,h in zip(bodies,headers)]
        rows=await self.dispatch_prebuilt(schedule,bodies,dest,headers,ids,built);after=await self.drain()
        events,integrity=load_events(self.trace)
        evidence=event_evidence(events,[x["response_id"] for x in rows],all(x["success"] for x in rows))
        summary=summarize_round(rows,n)
        burst_rows=rows[1:] if schedule=="P" else rows
        send=[r["client_send_monotonic"] for r in burst_rows if r["client_send_monotonic"] is not None]
        spread=max(send)-min(send) if send else None
        native_rids=set(evidence["request_mapping"].values())
        stepmap={x["step_id"]:x for x in events if x["kind"]=="scheduled_step"}
        first_service=[i for i in evidence["service_intervals"] if any(stepmap[i["step_id"]]["scheduled_tokens"].get(r,0)>1 for r in i["request_ids"])]
        durations=[(x["end_ns"]-x["begin_ns"])/1e9 for x in first_service]
        prefill_diagnostic_reference=require_current_reference(self.prefill_reference,self.label)
        engineering_ok=(schedule=="S" or burst_quality(spread,prefill_diagnostic_reference))
        summary.update({**info,"phase":"formal","schema_version":"G-round-v2","model":self.label,"round_id":info["logical_id"],"attempt_id":rid,"repeat_id":repeat_id,"attempt_index":info["attempt_index"],
             "retry_of":info["retry_of"],"retry_reason":info["retry_reason"],"approved_revision":info["approved_revision"],"source_revision":info["source_revision"],"document_id":doc["id"],"N":n,"schedule":schedule,"arm":arm,"run_position":position,"tool_call_count":len(calls),
             "evidence":evidence,"trace_prefixes":integrity,"burst_send_spread":spread if schedule!="S" else None,
             "burst_relative_to_cold_request_latency":spread/prefill_diagnostic_reference if spread is not None else None,
             "burst_relative_to_prefill_service":spread/prefill_diagnostic_reference if spread is not None else None,
             "burst_relative_reference_caveat":"sum of cold-single executor prefill service intervals; includes dispatch/IPC, excludes decode; not GPU kernel duration",
             "engineering_burst_quality_passed":engineering_ok,"prefill_reference":self.prefill_reference,
             "historical_burst_comparison_only":(schedule=="S" or burst_quality(spread,FROZEN_PREFILL_REFERENCE[self.label])),"finished_utc":now(),"after_metrics":after})
        # Entire round retained even when engineering quality or overlap is unfavorable.
        save(dest/"result.json",summary);append(self.folder/"round_log.jsonl",summary);self.rounds.append(summary)
        save(self.folder/"status.json",{"phase":"formal","completed_rounds":len(self.rounds),"planned_rounds":1620,"total_model_requests_including_calibration":self.request_count,
             "latest_round":rid,"latest_complete":summary["complete"],"latest_overlap":evidence["backend_overlap_status"],"updated_utc":now()},replace=True)
        print("FORMAL_ROUND",self.label,len(self.rounds),rid,summary["complete"],evidence["backend_overlap_status"],flush=True)
        return summary

    async def overhead(self):
        d=self.calibration_doc;base=messages(self.inputs["S"],self.inputs["markers"][0]+"\n"+d["G"],1)
        flag=Path(os.environ["G_TRACE_FLAG"]);records=[]
        for pair in range(3):
            order=["on","off"] if pair%2 else ["off","on"]
            for mode in order:
                name=f"overhead-{pair}-{mode}";dest=self.folder/"calibration"/name;dest.mkdir(parents=True)
                await self.reset(dest)
                flag.write_text("1" if mode=="on" else "0")
                append(self.folder/"trace-policy-events.jsonl",{"mode":mode,"purpose":"paired logger-write overhead diagnostic","pair":pair,"utc":now()})
                await asyncio.sleep(.25)
                body=self.body(base,uuid.uuid4().hex);save(dest/"0001.request.json",body)
                row=await self.send(body,dest,1,f"G-{self.run_id}-{name}",self.offline_ids(base))
                await self.drain()
                flag.write_text("1");await asyncio.sleep(.25)
                assert row["success"] and row["metric_valid"] and row["h_i"]==0
                out={"mode":mode,"pair":pair,"row":row,"scope":"same private hooks, logging writes enabled/disabled; not total zero-hook causal overhead"}
                save(dest/"result.json",out);records.append(out)
        on=[x["row"]["request_latency"] for x in records if x["mode"]=="on"]
        off=[x["row"]["request_latency"] for x in records if x["mode"]=="off"]
        save(self.folder/"trace-overhead.json",{"records":records,"paired_diagnostic_requests":6,"on_median":statistics.median(on),"off_median":statistics.median(off),
             "median_latency_ratio_on_off":statistics.median(on)/statistics.median(off),
             "limitations":"3 pairs, noisy descriptive estimate; wrappers remain present in off mode; all pilot cells use write-enabled trace"})
        print("TRACE_OVERHEAD_DIAGNOSTIC_COMPLETE",self.label,flush=True)
