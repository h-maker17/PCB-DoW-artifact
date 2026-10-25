"""Production formal request backend, using a fresh independent segment."""
import pathlib, os, json, sys, asyncio, httpx
from transformers import AutoTokenizer
from gengine import Engine
from gcommon import ROOT
from ganalysis import load_events
from formal_core import read, save, sha, utc, validate_round


class RealBackend:
    def __init__(self, rev, experiment_root, segment, manifest, guard):
        self.rev = pathlib.Path(rev);
        self.root = pathlib.Path(experiment_root);
        self.segment = segment;
        self.m = manifest
        e = Engine();
        self.e = e;
        e.label = 'llama';
        e.run_id = segment;
        e.folder = self.root / 'segments' / segment;
        e.folder.mkdir(parents=True, exist_ok=True);
        e.round_folder = self.root;
        e.trace = e.folder / 'traces';
        e.model = 'G-llama';
        e.kwargs = {};
        e.experiment_id = manifest['experiment_id'];
        e.active_info = None;
        e.capture_sends = [];
        e.guard = guard
        e.docs = read(self.rev / 'data/documents.json');
        e.calibration_doc = read(self.rev / 'data/pilot-documents.json')[0];
        e.inputs = read(self.rev / 'data/inputs.json')
        e.tokenizer = AutoTokenizer.from_pretrained(os.environ.get('PCB_LLAMA_TOKENIZER', os.path.join(
            os.environ.get('PCB_MODEL_DIR', 'models'), 'Llama-3.3-70B-Instruct-FP8')), local_files_only=True)
        limits = httpx.Limits(max_connections=32, max_keepalive_connections=32, keepalive_expiry=120);
        e.transport = httpx.AsyncHTTPTransport(retries=0, limits=limits);
        e.client = httpx.AsyncClient(base_url='http://127.0.0.1:18021', timeout=600, transport=e.transport,
                                     trust_env=False)
        from gslots import SlotClients
        e.slots = SlotClients('http://127.0.0.1:18021', timeout=600);
        e.prefill_reference = None;
        e.request_count = 0;
        e.calibration = [];
        e.rounds = []

    async def startup_checks(self):
        e = self.e;
        e.guard();
        events, _ = load_events(e.trace);
        layouts = [x for x in events if x['kind'] == 'cache_layout']
        if not layouts or not layouts[-1]['actual_dtype_known'] or any(
            g['dtype'] != 'torch.bfloat16' for g in layouts[-1]['groups']): raise ValueError(
            'Formal resolved BF16 KV missing/different')
        save(e.folder / 'actual-KV-layout.json', layouts[-1]);
        server = read(e.folder / 'patch/actual-HTTP-idle-server.json');
        e.slots.ensure(20)
        policy = dict(server=server, control_client_seconds=e.transport._pool._keepalive_expiry,
                      slot_client_seconds=[t._pool._keepalive_expiry for t in e.slots.transports],
                      actual_runtime_objects_verified=True)
        if policy['server']['server_keepalive_seconds'] != 600 or policy['control_client_seconds'] != 120 or policy[
            'slot_client_seconds'] != [120] * 20: raise ValueError('Actual HTTP idle policy differs')
        save(e.folder / 'actual-HTTP-idle-policy.json', policy);
        await e.calibrate();
        await e.overhead()
        if e.request_count != 25: raise ValueError('Unexpected startup diagnostic request count; no formal')

    async def execute(self, cell, info, dest, guard):
        e = self.e;
        e.active_info = info;
        e.capture_sends = [];
        e.guard = guard
        doc = next(d for d in e.docs if d['id'] == cell['document_id'])
        try:
            row = await e._round_attempt(doc, cell['N'], cell['schedule'], cell['arm'], cell['position'],
                                         cell['repeat_id'], info)
        except BaseException as exc:
            self.failure_class = 'transport_protocol_failure' if isinstance(exc,
                                                                            httpx.RemoteProtocolError) and not e.capture_sends else 'timeout' if isinstance(
                exc, httpx.TimeoutException) else 'engine_failure'
            raise
        req = [read(p) for p in sorted(dest.glob('*.request-record.json'))];
        ids = {r['replica_id']: read(dest / f"{r['replica_id']:04d}.planned.json")['expected_ids'] for r in req}
        return row, req, read(dest / 'isolation.json'), ids, e.prefill_reference['seconds']

    async def drain_after(self, cell, row):
        await self.e.drain()

    def failure_snapshot(self):
        sends = self.e.capture_sends
        # Returning captures identify actual writes; interrupted process is recovered as unknown.
        return dict(sent_count_known=True, sent_requests=sum(s is not None for s in sends),
                    failure_class=getattr(self, 'failure_class', 'engine_failure'))

    async def close(self):
        await self.e.slots.aclose();await self.e.client.aclose()


async def main():
    from formal_core import gate, verify_source, Runner
    from gchecks import require_disk
    rev = pathlib.Path(__file__).resolve().parent.parent;
    approval = pathlib.Path(os.environ['G_FORMAL_APPROVAL']);
    m = gate(rev, approval);
    segment = sys.argv[1];
    root = ROOT / 'formal' / m['experiment_id'];
    root.mkdir(parents=True, exist_ok=True)
    a = read(approval)

    def guard():
        require_disk(ROOT);gate(rev, approval)

    backend = RealBackend(rev, root, segment, m, guard)
    try:
        await backend.startup_checks();
        runner = Runner(root, m, read(rev / 'schedule.json'), verify_source(rev), backend, guard)
        result = await runner.run(segment, a.get('retry_decisions'), a.get('resume_review'))
        save(root / 'segments' / segment / 'completion.json',
             dict(result, model_requests_in_segment_including_checks=backend.e.request_count, finished_utc=utc()))
    finally:
        await backend.close()


if __name__ == '__main__': asyncio.run(main())
