"""G-only transport burst preparation; no model, metric or threshold changes."""
import asyncio,time
from importlib.metadata import version

async def ready_group(indices,sender):
    ready=0;all_ready=asyncio.Event();release=asyncio.Event()
    async def run(i):
        nonlocal ready
        ready+=1
        if ready==len(indices):all_ready.set()
        await release.wait()
        return await sender(i)
    tasks=[asyncio.create_task(run(i)) for i in indices]
    try:
        await all_ready.wait()
        released=time.monotonic()
        release.set()
        return list(await asyncio.gather(*tasks)),released
    finally:
        for task in tasks:
            if not task.done():task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)

async def dispatch_capture(schedule,n,sender,prepare=None):
    results=[];groups=[]
    preparation=await prepare(list(range(n))) if prepare else None
    async def group(indices,burst):
        prep=preparation
        captured,released=await ready_group(indices,sender)
        groups.append({"indices":list(indices),"burst":burst,"release_monotonic":released,"preparation":prep,
                       "all_transport_returned_monotonic":time.monotonic()})
        results.extend(captured)
    if schedule=="S":
        for i in range(n):await group([i],False)
    elif schedule=="P":
        # Warm before the FIRST request; no health, parsing or I/O after it completes.
        prep=preparation
        if n==1:await group([0],False)
        else:
            release=asyncio.Event();ready=asyncio.Event();count=0
            async def remaining(i):
                nonlocal count
                count+=1
                if count==n-1:ready.set()
                await release.wait()
                return await sender(i)
            tasks=[asyncio.create_task(remaining(i)) for i in range(1,n)]
            try:
                await ready.wait()
                first_release=time.monotonic();first=await sender(0)
                fanout_release=time.monotonic();release.set()
                results.append(first)
                rest=list(await asyncio.gather(*tasks));results.extend(rest)
                groups.extend([{"indices":[0],"burst":False,"release_monotonic":first_release,
                                "preparation":prep,"all_transport_returned_monotonic":fanout_release},
                               {"indices":list(range(1,n)),"burst":True,"release_monotonic":fanout_release,
                                "preparation":prep,"all_transport_returned_monotonic":time.monotonic(),
                                "pool_warmed_before_first":True,"health_between_first_and_fanout":False}])
            finally:
                for task in tasks:
                    if not task.done():task.cancel()
                await asyncio.gather(*tasks,return_exceptions=True)
    elif schedule=="C":await group(list(range(n)),True)
    else:raise ValueError(schedule)
    return results,groups

def pool_snapshot(transport):
    # Pinned, private-client read-only introspection; missing access is an error.
    if version("httpx")!="0.28.1" or version("httpcore")!="1.0.9":
        raise RuntimeError("Unaudited HTTP transport version")
    pool=transport._pool
    conns=list(pool.connections)
    return {"connections":len(conns),"idle_not_expired":sum(c.is_idle() and not c.has_expired() for c in conns),
            "closed":sum(c.is_closed() for c in conns),"pending_pool_requests":len(pool._requests),
            "max_connections":pool._max_connections,"httpx":version("httpx"),"httpcore":version("httpcore"),
            "introspection_scope":"private client boundary snapshot; not per-request queue duration"}

async def warm_available_connections(client,transport,count):
    before=pool_snapshot(transport);events=[]
    async def one(i):
        request=client.build_request("GET","/health")
        async def trace(name,info):events.append({"replica":i,"name":name,"t":time.monotonic()})
        request.extensions["trace"]=trace
        response=await client.send(request);response.raise_for_status()
        return response.status_code
    _,released=await ready_group(list(range(count)),one)
    after=pool_snapshot(transport)
    if after["idle_not_expired"]<count or after["pending_pool_requests"]!=0:
        raise RuntimeError("Insufficient ready idle connections before burst; do not send model requests")
    return {"count":count,"health_group_release":released,"before":before,"after":after,"health_trace":events,
            "model_requests":0,"HTTP_retries":0}

async def capture_http(client,built_request):
    invoked=time.monotonic();events=[];times={};raw=None;status=None;error=None
    async def trace(name,info):
        t=time.monotonic()
        events.append({"name":name,"t":t})
        if name=="http11.send_request_headers.started":times.setdefault("send",t)
        if name=="http11.receive_response_body.complete":times.setdefault("done",t)
    built_request.extensions["trace"]=trace
    try:
        response=await client.send(built_request)
        times.setdefault("done",time.monotonic())
        status=response.status_code;raw=response.content
        if status!=200:error="HTTP_"+str(status)
    except Exception as exc:
        times.setdefault("done",time.monotonic());error=type(exc).__name__
    return {"times":times,"raw_bytes":raw,"status":status,"error":error,"invoked":invoked,
            "returned":time.monotonic(),"events":events}

def burst_quality(spread,prefill):
    # Original frozen thresholds. No outcome-dependent tuning.
    return spread is not None and spread<=.05 and spread<=.25*prefill
