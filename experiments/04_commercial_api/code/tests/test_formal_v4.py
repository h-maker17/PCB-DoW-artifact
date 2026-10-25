"""Offline only: frozen real history prefix, clearly synthetic new responses."""
from pathlib import Path
from datetime import datetime, timezone, timedelta
from decimal import Decimal as D
import sys, json, copy, hashlib, shutil, sqlite3, uuid, socket, urllib.request, getpass, unittest

B = Path(__file__).resolve().parents[1];
R = B.parents[1];
sys.dont_write_bytecode = True;
sys.path.insert(0, str(B / 'code'))
COUNTERS = {'network': 0, 'prompt': 0}


def no_network(*a, **k): COUNTERS['network'] += 1;raise AssertionError('No network')


def no_prompt(*a, **k): COUNTERS['prompt'] += 1;raise AssertionError('No prompt')


socket.socket.connect = no_network;
socket.create_connection = no_network;
urllib.request.OpenerDirector.open = no_network;
getpass.getpass = no_prompt
import formal_v4 as m
import formal_v4_statistics as stats

RULE = json.loads((B / 'config/runtime_policy/formal_v4_ds700_only_20261013.json').read_text(encoding='utf-8'));
T = datetime(2026, 10, 13, 15, tzinfo=timezone.utc)


def read(p): return json.loads(p.read_text(encoding='utf-8'))


def put(root, rel, v):
    p = root / rel;
    p.parent.mkdir(parents=True, exist_ok=True);
    p.write_text(m.core.canonical(v), encoding='utf-8');
    return p


def raw(n=2200, out=2, hit=0): return {'id': 'SYNTHETIC-FORMAL-RESPONSE', 'model': 'deepseek-v4-pro',
                                       'created': int(T.timestamp()) - 5, 'choices': [
        {'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': 'SYNTHETIC'}}],
                                       'usage': {'prompt_tokens': n, 'prompt_cache_hit_tokens': hit,
                                                 'prompt_cache_miss_tokens': n - hit, 'completion_tokens': out,
                                                 'total_tokens': n + out}}


def timing(t=T): return {'client_start_utc': t.isoformat(),
                         'physical_open_started_utc': (t + timedelta(milliseconds=20)).isoformat(),
                         'response_received_utc': (t + timedelta(seconds=2)).isoformat(),
                         'elapsed_monotonic_seconds': 2, 'http_status': 200}


def fixture():
    root = B / 'fixtures' / ('s-' + uuid.uuid4().hex[:8]);
    (root / 'code').mkdir(parents=True)
    for n in m.SOURCE_NAMES: shutil.copyfile(B / 'code' / n, root / 'code' / n)
    config = read(B / RULE['config_path']);
    plan = read(B / RULE['plan_path']);
    baseline = read(B / RULE['baseline_path'])
    for rel, h in baseline['prior_bound_files'].items():
        p = root / rel;
        p.parent.mkdir(parents=True, exist_ok=True);
        shutil.copyfile(R / rel, p)
    for rel, h in baseline['anchor_hashes'].items():
        p = root / rel;
        p.parent.mkdir(parents=True, exist_ok=True);
        shutil.copyfile(R / rel, p)
    for n, snap in baseline['databases'].items():
        p = root / 'logs' / n;
        p.parent.mkdir(exist_ok=True);
        db = sqlite3.connect(p);
        db.execute('CREATE TABLE meta(id INTEGER PRIMARY KEY,binding TEXT)');
        db.executemany('INSERT INTO meta VALUES(?,?)', snap['meta_rows']);
        db.execute(
            'CREATE TABLE events(seq INTEGER PRIMARY KEY AUTOINCREMENT,kind TEXT,data TEXT,prev TEXT,hash TEXT)');
        db.executemany('INSERT INTO events VALUES(?,?,?,?,?)', snap['rows']);
        db.commit();
        db.close()
    rule = copy.deepcopy(RULE);
    rule['source_hashes'] = {n: m.digest(root / 'code' / n) for n in m.SOURCE_NAMES};
    rule['evidence_source_hashes'] = {}
    for rel in (rule['config_path'], rule['plan_path'], *[rule[k + '_path'] for k in
                                                          ('baseline', 'contract', 'method', 'authorization',
                                                           'statistics', 'schedule', 'original_schedule')]):
        p = root / rel;
        p.parent.mkdir(parents=True, exist_ok=True);
        shutil.copyfile(B / rel, p)
    rp = 'config/runtime_policy/formal_v4_ds700_only_20261013.json';
    put(root, rp, rule)
    review = {'kind': m.KIND, 'fixture': True, 'approved': True, 'scope': m.SCOPE, 'new_request_allowance': 700,
              'OpenAI_generation_allowance': 0, 'paid_later_stages_allowed': False, 'rule_path': rp,
              'rule_sha256': m.digest(root / rp), 'source_hashes': rule['source_hashes']}
    for k in ('baseline', 'contract', 'method', 'authorization', 'statistics', 'schedule', 'original_schedule'): review[
        k + '_sha256'] = m.digest(root / rule[k + '_path'])
    rv = 'review/SYNTHETIC_ONLY_APPROVAL.json';
    put(root, rv, review);
    return root, rv


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.root, self.rv = fixture();self.a = m.Authority(self.root, self.rv, fixture=True)

    def tearDown(self):
        self.a.close()

    def cadence(self):
        wall = [T + timedelta(seconds=3) if self.a.state()['observations'] else T];
        mono = [10.0];
        self.a.cadence_anchor = None

        def sleep(n): mono[0] += n;wall[0] += timedelta(seconds=n)

        return self.a.wait_cadence(self.a.state()['next_id'], sleep, lambda: mono[0], lambda: wall[0])

    def accept(self, data=None, t=None):
        self.a.initialize();
        rid = self.a.state()['next_id'];
        self.cadence();
        self.a.reserve(rid, 'SYNTHETIC', T);
        self.a.transport_start(rid);
        p = put(self.root, 'analysis/formal_v4_runs/SYNTHETIC/' + rid + '/response.json', data or raw());
        return self.a.complete_observation(rid, p, t or timing())

    def rejected(self, code, fn):
        with self.assertRaises((m.Stop, m.EvidenceError)) as ctx: fn()
        self.assertEqual(str(ctx.exception), code)

    def test_exact700_and_old_states_preserved(self):
        self.assertEqual(len(self.a.ids), 700);
        self.assertEqual(self.a.ids[0], 'deepseek-n5-r1-frontmeta-i1');
        self.assertEqual(self.a.totals('deepseek')['budget_exposure'], '9.6339252');
        self.assertEqual(self.a.totals('openai')['budget_exposure'], '26.2548');
        self.a.initialize();
        self.a.initialize();
        self.assertEqual(len(m.chain(self.a.cash)), 88);
        self.assertEqual(len(m.chain(self.a.workflow)), 67)

    def test_all_non_DS_formal_scopes_denied(self):
        for row in self.a.plan.values():
            if row['request_id'] not in self.a.ids: self.rejected('SCOPE_CURSOR_OR_STOP',
                                                                  lambda row=row: self.a.preflight(row['request_id'],
                                                                                                   T))

    def test_old_approval_cannot_formal(self):
        r = read(self.root / self.rv);
        r['kind'] = 'PILOT_V3_PRODUCTION_APPROVAL';
        put(self.root, 'review/WRONG.json', r);
        self.rejected('NO_EXACT_FORMAL_PERMISSION', lambda: m.Authority(self.root, 'review/WRONG.json', fixture=True))

    def test_fixture_cannot_be_production(self):
        self.rejected('PRODUCTION_ROOT_REQUIRED', lambda: m.Authority(self.root, self.rv))

    def test_reserve_context_and_nextpeak(self):
        reserve, row = self.a.preflight(self.a.ids[0], T);
        self.assertEqual(reserve, D('9.438912'));
        self.assertEqual(reserve + D(self.a.totals('deepseek')['budget_exposure']), D('19.0728372'))

    def test_budget_no_newreserve(self):
        self.a.totals = lambda p: {'budget_exposure': '20'};
        self.rejected('NEXT_RESERVE_EXCEEDS_STOP', lambda: self.a.preflight(self.a.ids[0], T));
        self.assertEqual(len(m.chain(self.a.cash)), 88)

    def test_success_conservation_not_pilot1cap(self):
        before = D(self.a.totals('deepseek')['budget_exposure']);
        v = self.accept();
        self.assertEqual(D(self.a.totals('deepseek')['budget_exposure']), before + D(v['lower']['cash']));
        self.assertFalse(self.a.state()['formal_completed']);
        self.assertTrue(self.a.state()['DeepSeek_formal_enabled'])

    def test_reserved_interruption_cannot_replay(self):
        self.a.initialize();
        self.cadence();
        self.a.reserve(self.a.ids[0], 'SYNTHETIC', T);
        self.a.close();
        self.a = m.Authority(self.root, self.rv, fixture=True);
        self.assertIsNone(self.a.state()['next_id']);
        self.assertEqual(self.a.totals('deepseek')['budget_exposure'], '19.0728372')

    def test_between_request_interrupt_only_unconsumed_next(self):
        self.accept();
        self.a.interrupted();
        self.a.close();
        self.a = m.Authority(self.root, self.rv, fixture=True);
        self.assertEqual(self.a.state()['next_id'], self.a.ids[1]);
        self.rejected('SCOPE_CURSOR_OR_STOP', lambda: self.a.preflight(self.a.ids[0], T))

    def test_unknown_terminal_no_OA(self):
        self.a.initialize();
        self.cadence();
        rid = self.a.ids[0];
        self.a.reserve(rid, 'SYNTHETIC', T);
        self.a.transport_start(rid);
        self.a.unknown(rid, 'SYNTHETIC_ERROR');
        self.assertIsNone(self.a.state()['next_id']);
        self.assertEqual(self.a.totals('deepseek')['budget_exposure'], '19.0728372');
        self.assertEqual(self.a.totals('openai')['budget_exposure'], '26.2548')

    def test_length_breach_cost_recorded_then_stop(self):
        self.rejected('MEASUREMENT_BREACH_ACCOUNTED_STOP', lambda: self.accept(raw(2501)));
        a = self.a.attempts()[self.a.attempt_id(self.a.ids[0])];
        self.assertEqual(a['status'], 'SETTLED');
        self.assertTrue(a['bound_breach']);
        self.assertIsNone(self.a.state()['next_id'])

    def test_output_breach(self):
        self.rejected('MEASUREMENT_BREACH_ACCOUNTED_STOP', lambda: self.accept(raw(out=65)))

    def test_model_drift_fullreserve(self):
        r = raw();
        r['model'] = 'wrong';
        self.rejected('RESPONSE_INVALID_RESERVE_RETAINED', lambda: self.accept(r));
        self.assertEqual(self.a.totals('deepseek')['budget_exposure'], '19.0728372')

    def test_usage_missing_never0(self):
        r = raw();
        del r['usage']['prompt_cache_hit_tokens'];
        self.rejected('RESPONSE_INVALID_RESERVE_RETAINED', lambda: self.accept(r))

    def test_nonconservation(self):
        r = raw();
        r['usage']['total_tokens'] += 1;
        self.rejected('RESPONSE_INVALID_RESERVE_RETAINED', lambda: self.accept(r))

    def test_calendar_missing_before_send(self):
        self.rejected('CALENDAR_NOT_COVERED', lambda: self.a.preflight(self.a.ids[0], datetime(2027, 1, 1,
                                                                                               tzinfo=timezone.utc)));self.assertEqual(
            len(m.chain(self.a.cash)), 88)

    def test_ambiguous_price_fullreserve(self):
        t = datetime(2026, 10, 13, 5, 59, 59, tzinfo=timezone.utc);
        r = raw();
        r['created'] = int(t.timestamp());
        self.rejected('PRICE_INTERVAL_RESERVE_RETAINED', lambda: self.accept(r, timing(t)));
        self.assertEqual(self.a.totals('deepseek')['budget_exposure'], '19.0728372')

    def test_settle_receipt_storage_failure_no_replay(self):
        original = m.append

        def fail(db, k, d):
            if k == 'FORMAL_V4_RECEIPT': raise OSError('SYNTHETIC_STORAGE')
            return original(db, k, d)

        m.append = fail
        try:
            with self.assertRaises(OSError):
                self.accept()
        finally:
            m.append = original
        self.assertIsNone(self.a.state()['next_id']);
        self.assertEqual(self.a.attempts()[self.a.attempt_id(self.a.ids[0])]['status'], 'SETTLED')

    def test_cadence_restart60_samearm10_block60(self):
        self.a.initialize();
        w = self.cadence();
        self.assertEqual(w['gap_seconds'], 60);
        self.accept();
        w = self.cadence();
        self.assertEqual(w['gap_seconds'], 10)

    def test_cadence_not_optional(self):
        self.a.initialize();self.rejected('CADENCE_WAIT_REQUIRED',
                                          lambda: self.a.reserve(self.a.ids[0], 'SYNTHETIC', T))

    def test_negative_nohit_still_valid(self):
        self.assertTrue(self.accept(raw(hit=0))['technical_accepted'])

    def test_completion_requires700_exact_receipts_notexit(self):
        self.accept();
        self.assertFalse(self.a.state()['formal_completed']);
        self.assertEqual(self.a.state()['unconsumed_ids'], 699)

    def test_700_completion_bulk_isolated_fixture(self):
        self.a.initialize()
        for rid in self.a.ids:
            row = self.a.plan[rid];
            aid = self.a.attempt_id(rid);
            p = put(self.root, 'analysis/formal_v4_runs/SYNTHETIC/' + rid + '/response.json', raw());
            v = m.pricing.value_response(raw(), 'deepseek', row['body']['model'], self.a.contract, timing());
            h = m.digest(p)
            m.append(self.a.cash, 'RESERVE',
                     {'attempt': aid, 'request_id': rid, 'provider': 'deepseek', 'currency': 'CNY', 'phase': 'formal',
                      'reserve': '9.438912', 'payload_hash': row['payload_hash']});
            m.append(self.a.cash, 'TRANSPORT_START', {'attempt': aid});
            m.append(self.a.cash, 'SETTLE', {'attempt': aid, **v['lower'], 'valuation_status': 'USAGE_PRICED_ESTIMATE'})
            m.append(self.a.workflow, 'FORMAL_V4_OBSERVATION', {'request_id': rid, 'payload_hash': row['payload_hash'],
                                                                'response_path': str(p.relative_to(self.root)),
                                                                'raw_sha256': h, 'timing': timing()});
            m.append(self.a.workflow, 'FORMAL_V4_RECEIPT',
                     {'request_id': rid, 'payload_hash': row['payload_hash'], 'value': v, 'technical_accepted': True})
        s = self.a.state();
        self.assertFalse(s['formal_completed']);
        self.assertIsNone(s['next_id']);
        before = len(m.chain(self.a.cash));
        self.a.finalize_checkpoint();
        self.a.finalize_checkpoint();
        s = self.a.state();
        self.assertTrue(s['formal_completed']);
        self.assertEqual(s['unconsumed_ids'], 0);
        self.assertEqual(len(m.chain(self.a.cash)), before)


class StatisticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = read(B / RULE['plan_path']);cls.policy = read(B / RULE['statistics_path'])

    def measurements(self):
        return {r['request_id']: {'input_cost': str(10 if r['arm'] == 'baseline' else 20), 'output_cost': '1',
                                  'input_tokens': 100, 'hit_tokens': 0, 'miss_tokens': 100,
                                  'uniform_peak_input_cost': str(10 if r['arm'] == 'baseline' else 20),
                                  'technical_accepted': True} for r in self.plan if
                r['provider'] == 'deepseek' and r['phase'] == 'formal'}

    def test_exactallassigned_primary_and_seed_pairbootstrap(self):
        v = stats.analyze(self.plan, self.measurements(), self.policy);
        p = v['primary']['actual_usage_priced_input_ratio_of_sums'];
        self.assertEqual(p['estimate'], '2');
        self.assertEqual(p['ci95'], ['2', '2']);
        self.assertEqual(p['matched_blocks'], 5);
        self.assertEqual(p['bootstrap_resamples'], 10000);
        self.assertEqual(p['seed'], 20261012);
        self.assertTrue(p['small_block_inference_exploratory'])

    def test_missingfirst_orblock_mainnull_never_subset(self):
        data = self.measurements();
        rid = next(r['request_id'] for r in self.plan if
                   r['provider'] == 'deepseek' and r['phase'] == 'formal' and r['N'] == 20 and r[
                       'arm'] == 'frontmeta' and r['repeat'] == 3 and r['replica'] == 1);
        del data[rid];
        v = stats.analyze(self.plan, data, self.policy);
        self.assertIsNone(v['primary']['actual_usage_priced_input_ratio_of_sums']['estimate']);
        self.assertIn(rid,
                      next(c for c in v['chains'] if c['N'] == 20 and c['arm'] == 'frontmeta' and c['repeat'] == 3)[
                          'missing_ids'])

    def test_ratioofsums_not_meanratios(self):
        blocks = [{'repeat': i + 1, 'complete': True, 'attack': str(a), 'baseline': str(b)} for i, (a, b) in
                  enumerate([(1, 1), (100, 10), (3, 1), (4, 1), (5, 1)])];
        v = stats.ratio_bootstrap(blocks, [1, 2, 3, 4, 5]);
        self.assertEqual(D(v['estimate']), D(113) / D(14))

    def test_pilot_not_included(self):
        data = self.measurements();
        data['deepseek-pilot-frontmeta-i1'] = {'input_cost': '999999'};
        v = stats.analyze(self.plan, data, self.policy);
        self.assertEqual(v['primary']['actual_usage_priced_input_ratio_of_sums']['estimate'], '2')

    def test_warmsecondary_never_changes_primary(self):
        data = self.measurements()
        for r in self.plan:
            if r['request_id'] in data and r['arm'] == 'baseline' and r['replica'] >= 3: data[r['request_id']][
                'hit_tokens'] = 90;data[r['request_id']]['miss_tokens'] = 10
        v = stats.analyze(self.plan, data, self.policy);
        self.assertEqual(v['primary']['actual_usage_priced_input_ratio_of_sums']['estimate'], '2');
        self.assertEqual(v['primary']['warm_subset_secondary_only']['selected_repeats'], [1, 2, 3, 4, 5])

    def test_actual_and_uniform_not_choose_larger(self):
        data = self.measurements()
        for r in self.plan:
            if r['request_id'] in data and r['arm'] == 'frontmeta': data[r['request_id']][
                'uniform_peak_input_cost'] = '100'
        v = stats.analyze(self.plan, data, self.policy);
        self.assertEqual(v['primary']['actual_usage_priced_input_ratio_of_sums']['estimate'], '2');
        self.assertEqual(v['primary']['uniform_peak_input_diagnostic']['estimate'], '10')


if __name__ == '__main__':
    suite = unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromTestCase(RuntimeTests),
                                unittest.defaultTestLoader.loadTestsFromTestCase(StatisticsTests)]);
    result = unittest.TextTestRunner(verbosity=2).run(suite);
    report = {'testsRun': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
              'safety_counters': COUNTERS, 'fixture_kind': 'SYNTHETIC_NEW_RESPONSES_OVER_FROZEN_REAL_HISTORY_PREFIX',
              'fixture_not_commercial_result': True};
    (B / 'SELF_TEST_RESULT.json').write_text(json.dumps(report, indent=2), encoding='utf-8');
    print(json.dumps(report));
    raise SystemExit(0 if result.wasSuccessful() and not any(COUNTERS.values()) else 1)
