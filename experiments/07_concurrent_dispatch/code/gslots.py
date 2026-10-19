"""One persistent, preconnected HTTP/1.1 pool per logical request slot; no KV warmup."""
import asyncio
import httpx
from gburst import ready_group,pool_snapshot,warm_available_connections

class SlotClients:
    def __init__(self,base_url,timeout=600,maximum_slots=20):
        self.base_url=str(base_url);self.timeout=timeout;self.maximum_slots=maximum_slots
        self.clients=[];self.transports=[]
    def ensure(self,count):
        if type(count) is not int or not 1<=count<=self.maximum_slots:
            raise RuntimeError("Invalid slot count; no model request")
        while len(self.clients)<count:
            transport=httpx.AsyncHTTPTransport(retries=0,limits=httpx.Limits(max_connections=1,max_keepalive_connections=1,keepalive_expiry=120))
            client=httpx.AsyncClient(base_url=self.base_url,transport=transport,timeout=self.timeout,trust_env=False)
            self.transports.append(transport);self.clients.append(client)
        return self.clients[:count]
    def snapshot(self,count=None):
        count=len(self.clients) if count is None else count
        rows=[pool_snapshot(t) for t in self.transports[:count]]
        return {"connections":sum(r["connections"] for r in rows),
                "idle_not_expired":sum(r["idle_not_expired"] for r in rows),
                "pending_pool_requests":sum(r["pending_pool_requests"] for r in rows),
                "closed":sum(r["closed"] for r in rows),
                "max_connections_per_slot":1,"max_keepalive_connections_per_slot":1,
                "slots":rows,"created_clients":len(self.clients)}
    async def prepare(self,count):
        self.ensure(count);before=self.snapshot(count)
        async def one(i):return await warm_available_connections(self.clients[i],self.transports[i],1)
        prepared,released=await ready_group(list(range(count)),one)
        after=self.snapshot(count)
        if after["idle_not_expired"]!=count or after["pending_pool_requests"]!=0:
            raise RuntimeError("Every slot requires its own ready idle connection")
        return {"count":count,"health_group_release":released,"before":before,"after":after,
                "slot_preparations":prepared,"policy":"one independent pool per slot, max_connections=1",
                "slot_allocation":"request index i uses slot i for S/P/C",
                "preparation_for_all_schedules":True,"model_requests":0,"HTTP_retries":0}
    async def aclose(self):
        await asyncio.gather(*(c.aclose() for c in self.clients))
