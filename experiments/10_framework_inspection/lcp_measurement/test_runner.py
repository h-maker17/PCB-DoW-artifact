"""Recovery acceptance runner: synthetic specimens and frozen controls only."""
from pathlib import Path
import argparse, copy, json, hashlib, ast, datetime, importlib.metadata, sys, traceback
from render_binding import ROOT, TEMPLATES, sha, object_hash, dump, Renderer
from input_authority import _issue, load_control, GateClosed
from fixtures import make_synthetic
from offline_entry import measure_episode, measure_run
from observer import Observer, physical_hits, token_span
from frozen_core import locate, token_lcp


def seal():
    files = {}
    for p in sorted(ROOT.glob('*.py')): files[p.relative_to(ROOT).as_posix()] = sha(p)
    for base in ('config', 'sources', 'templates', 'negative_source_probe'):
        for p in sorted((ROOT / base).rglob('*')):
            if p.is_file() and p.name != 'runtime_manifest.json': files[p.relative_to(ROOT).as_posix()] = sha(p)
    packages = {x: importlib.metadata.version(x) for x in ('transformers', 'tokenizers', 'jinja2')}
    dump(ROOT / 'config/runtime_manifest.json',
         {'purpose': 'instrument_freeze_before_offline_test; actual_archive_gate_closed', 'python': sys.version,
          'files': files, 'packages': packages})
    print('Runtime sealed', sha(ROOT / 'config/runtime_manifest.json'), flush=True)


def compact(result):
    rows = []
    for ri, run in enumerate(result.get('runs', [])):
        for phase, ts in run['proofs'].items():
            for t, items in ts.items():
                for i, item in enumerate(items): rows.append(
                    {'repeat': ri + 1, 'phase': phase, 'template': t, 'replica': i, **item})
    return {k: v for k, v in result.items() if k not in ('runs',)} | {'locations_and_proofs': rows}


def run_cases(renderer):
    out = []
    frozen = json.loads((ROOT / 'config/synthetic_expected.json').read_text(encoding='utf8'))['cases']
    extra = json.loads((ROOT / 'config/additional_expected.json').read_text(encoding='utf8'))['cases']
    for case in frozen + extra:
        e = make_synthetic(case['id'], renderer);
        r = measure_episode(ROOT, e, renderer)
        cls = {t: v['class'] for t, v in (r['classification'] or {}).items()}
        ok = r['status'] == case['expected_status'] and (
            set(cls) == set(TEMPLATES) and all(v == case['expected_class'] for v in cls.values()) if case[
                                                                                                         'expected_class'] is not None else
            r['classification'] is None)
        if 'expected_observer_calls' in case: ok &= r['observer_calls'] == case['expected_observer_calls']
        row = {'id': case['id'], 'expected': case, 'passed': bool(ok), 'observed_classes': cls, 'result': compact(r)};
        out.append(row)
        print('CASE', case['id'], 'PASS' if ok else 'FAIL', r['status'], cls, flush=True)
    dump(ROOT / 'results/synthetic_results.json', out)
    return out


def run_controls(renderer):
    m = json.loads((ROOT / 'config/control_manifest.json').read_text(encoding='utf8'))
    expected = {x['id']: x['expected'] for x in
                json.loads((ROOT / 'config/controls_expected.json').read_text(encoding='utf8'))['controls']}
    out = []
    for variant, control in sorted({(x['variant'], x['control']) for x in m['files']}):
        e = load_control(variant, control);
        r = measure_episode(ROOT, e, renderer)
        cls = {t: v['class'] for t, v in (r['classification'] or {}).items()}
        ok = r['status'] == 'measured' and set(cls) == set(TEMPLATES) and all(
            v == expected[control] for v in cls.values())
        out.append({'variant': variant, 'control': control, 'expected': expected[control], 'passed': ok,
                    'observed_classes': cls, 'result': compact(r)})
        print('CONTROL', variant, control, 'PASS' if ok else 'FAIL', r['status'], cls, flush=True)
    dump(ROOT / 'results/control_results.json', out);
    return out


def gate_tests(renderer):
    rows = []

    def check(name, result):
        ok = result.get('status') == 'gate_closed' and result.get('classification') is None and result.get(
            'observer_calls') == 0
        rows.append({'id': name, 'passed': ok, 'result': result});
        print('GATE', name, 'PASS' if ok else 'FAIL', flush=True)

    check('unissued_archive_mislabeled_synthetic',
          measure_episode(ROOT, {'scope': 'synthetic', 'runs': [{'pure_synthetic_fake_archive': True}]}, renderer))
    e = make_synthetic('empty_join_G_before_S', renderer)
    bad = _issue(e.runs, 'constructed_control', 'empty_join', e.history, e.queries, e.receipt)
    check('scope_contract_mismatch', measure_episode(ROOT, bad, renderer))
    try:
        load_control('unknown_variant', 'unknown_control');r = {'status': 'unexpected_success'}
    except GateClosed as exc:
        r = {'status': 'gate_closed', 'classification': None, 'observer_calls': 0, 'reason': str(exc)}
    check('unregistered_control', r)
    for name, field in [('mutated_final_messages', 'messages'), ('mutated_rendered_outside_G_S', 'rendered'),
                        ('mutated_token_ids_same_length', 'token_ids'), ('mutated_offsets_legal_range', 'offsets'),
                        ('template_mislabeled', 'template')]:
        e = make_synthetic('empty_join_G_before_S', renderer);
        item = e.runs[0]['measurements']['benign']['llama33']['final'][0]
        if field == 'messages': item['messages'][-1]['content'] += 'changed'
        if field == 'rendered': item['rendered'] = 'X' + item['rendered'][1:]
        if field == 'token_ids': item['token_ids'][0] += 1
        if field == 'offsets': item['offsets'][1] = [0, 1]
        if field == 'template': e.runs[0]['measurements']['benign']['llama33'] = e.runs[0]['measurements']['benign'][
            'qwen3']
        check(name, measure_episode(ROOT, e, renderer))
    # Check both input SHA and whole-runtime gates against reversible corruption of recovery copies only.
    m = json.loads((ROOT / 'config/control_manifest.json').read_text(encoding='utf8'));
    x = m['files'][0]
    p = ROOT / 'control_inputs' / (hashlib.sha256(x['file'].encode('utf8')).hexdigest()[:24] + '.json')
    e = load_control(x['variant'], x['control']);
    raw = p.read_bytes()
    try:
        p.write_bytes(raw + b'\n');r = measure_episode(ROOT, e, renderer)
    finally:
        p.write_bytes(raw)
    check('control_path_SHA_changed', r)
    for name, rel in [('registry_tamper', 'config/source_registry.json'), ('policy_tamper', 'config/policy.json'),
                      ('runtime_code_tamper', 'observer.py')]:
        e = make_synthetic('empty_join_G_before_S', renderer);
        p = ROOT / rel;
        raw = p.read_bytes()
        try:
            p.write_bytes(raw + b'\n');r = measure_episode(ROOT, e, renderer)
        finally:
            p.write_bytes(raw)
        check(name, r)
    try:
        measure_run(ROOT, {}, 'empty_join', '', '', 'synthetic');r = {'status': 'unexpected_success'}
    except GateClosed as exc:
        r = {'status': 'gate_closed', 'classification': None, 'observer_calls': 0, 'reason': str(exc)}
    check('standalone_run_classification', r)

    for name, kind in [('reissued_wrong_same_scope_contract', 'contract'), ('reissued_modified_runs', 'runs'),
                       ('reissued_modified_receipt', 'receipt'), ('reissued_wrong_source_root', 'root'),
                       ('reissued_wrong_queries', 'queries')]:
        e = make_synthetic('empty_join_G_before_S', renderer);
        runs = copy.deepcopy(e.runs);
        receipt = copy.deepcopy(e.receipt);
        contract = e.contract;
        root = e.source_root;
        queries = e.queries
        if kind == 'contract': contract = 'newline_join';receipt['assembly'] = contract
        if kind == 'runs': runs[0]['measurements']['benign']['llama33']['final'][0]['token_ids'][0] += 1;receipt[
            'runs_sha256'] = object_hash(runs)
        if kind == 'receipt': receipt['input_chunks_sha256'] = '0' * 64
        if kind == 'root': root = ROOT / 'negative_source_probe'
        if kind == 'queries': queries = []
        bad = _issue(runs, e.scope, contract, e.history, queries, receipt, root)
        check(name, measure_episode(ROOT, bad, renderer))
    e = make_synthetic('empty_join_G_before_S', renderer);
    check('untrusted_renderer',
          measure_episode(ROOT, e, type('UntrustedRenderer', (), {'binding_error': lambda *args: None})()))
    x = json.loads((ROOT / 'config/control_manifest.json').read_text(encoding='utf8'))['files'][0];
    e = load_control(x['variant'], x['control']);
    bad = _issue(e.runs, e.scope, e.contract, e.history, [], e.receipt);
    check('reissued_control_wrong_queries', measure_episode(ROOT, bad, renderer))

    x = json.loads((ROOT / 'config/control_manifest.json').read_text(encoding='utf8'))['files'][0];
    e = load_control(x['variant'], x['control']);
    receipt = copy.deepcopy(e.receipt);
    receipt['inputs'] = [receipt['inputs'][0], copy.deepcopy(receipt['inputs'][0])]
    bad = _issue([e.runs[0], copy.deepcopy(e.runs[0])], e.scope, e.contract, e.history, e.queries, receipt);
    check('duplicated_control_repeat_receipt', measure_episode(ROOT, bad, renderer))
    y = next(v for v in json.loads((ROOT / 'config/control_manifest.json').read_text(encoding='utf8'))['files'] if
             v['variant'] == x['variant'] and v['control'] != x['control']);
    other = load_control(y['variant'], y['control']);
    receipt = copy.deepcopy(e.receipt);
    receipt['inputs'][1] = other.receipt['inputs'][1]
    bad = _issue([e.runs[0], other.runs[1]], e.scope, e.contract, e.history, e.queries, receipt);
    check('mixed_control_identity_receipt', measure_episode(ROOT, bad, renderer))
    e = make_synthetic('tampered_source_SHA', renderer);
    p = ROOT / 'negative_source_probe/sources/assembly_sources.py';
    raw = p.read_bytes()
    try:
        p.write_bytes((ROOT / 'sources/assembly_sources.py').read_bytes());r = measure_episode(ROOT, e, renderer)
    finally:
        p.write_bytes(raw)
    check('negative_source_probe_byte_identity', r)
    dump(ROOT / 'results/gate_results.json', rows);
    return rows


def probes(renderer):
    rows = []

    def add(name, passed, evidence):
        rows.append({'id': name, 'passed': bool(passed), 'evidence': evidence})

    e = make_synthetic('empty_join_G_before_S', renderer);
    new = measure_episode(ROOT, e, renderer)
    failures = []
    for phase in ('benign', 'attacked'):
        for t in TEMPLATES:
            for item in e.runs[0]['measurements'][phase][t]['final']:
                native = e.runs[0]['trace'][phase]['returns'][item['replica']]
                old = locate(item['rendered'], '\n\n'.join(native['raw_chunks']), item['offsets'])
                failures.append({'phase': phase, 'template': t, 'replica': item['replica'], 'old': old})
    add('old_empty_join_failure_new_success', all(x['old']['status'] == 'ambiguous' for x in failures) and all(
        x['class'] == 'full_collapse' for x in new['classification'].values()), failures)
    e = make_synthetic('reordered_attack_marker_internal', renderer);
    obs = Observer(ROOT);
    got = []
    for t in TEMPLATES:
        item = copy.deepcopy(e.runs[0]['measurements']['benign'][t]['final'][0]);
        native = e.runs[0]['trace']['benign']['returns'][0];
        item['request'] = e.runs[0]['trace']['benign']['requests'][0]
        binding = {'call_key': native['call_key'], 'source_replica': 0, 'target_replica': 0, 'request_round': 0}
        got.append(obs.observe(item, native, binding, 'reversed_join', e.history, e.queries, 'benign'))
    add('unmarked_reordered_success', all(x['G']['status'] == 'ok' and x['G']['reordered'] for x in got), got)
    hits = physical_hits('prefix ASCII suffix', 'ASCII');
    add('ascii_encoding_dedup', len(hits) == 1 and set(hits[0]['encodings']) == {'identity', 'json_string_utf8'}, hits)
    hits = physical_hits('aaaa', 'aaa');
    add('overlapping_byte_occurrences', len(hits) == 2, hits)
    add('zero_width_special_token', token_span([0, 1], [[0, 0], [0, 1], [1, 1]]) == [1, 2],
        token_span([0, 1], [[0, 0], [0, 1], [1, 1]]))
    e = make_synthetic('empty_join_G_before_S', renderer);
    r = measure_episode(ROOT, e, renderer);
    got = []
    for t in TEMPLATES:
        item = e.runs[0]['measurements']['benign'][t]['final'][0];
        g = r['runs'][0]['proofs']['benign'][t][0]['G'];
        s = r['runs'][0]['proofs']['benign'][t][0]['S']
        got.append(
            {'template': t, 'complete_text_equal': item['rendered'][s['chars'][0]:s['chars'][1]] == e.history, 'S': s,
             'history_length': len(e.history), 'G': g})
    add('complete_S_preserved', all(x['complete_text_equal'] for x in got), got)
    # Find adjacent actual Unicode spans sharing a real tokenizer token without inventing offsets.
    got = []
    for t in TEMPLATES:
        tok = renderer.tokenizers[t];
        text = 'Antidisestablishmentarianism';
        enc = tok(text, add_special_tokens=False, return_offsets_mapping=True)
        offset = next((x for x in enc['offset_mapping'] if x[1] - x[0] >= 2), None)
        a, b = offset;
        split = a + 1;
        left = token_span([a, split], enc['offset_mapping']);
        right = token_span([split, b], enc['offset_mapping'])
        got.append({'template': t, 'text': text, 'actual_offsets': [list(x) for x in enc['offset_mapping']],
                    'adjacent_chars': [[a, split], [split, b]], 'tokens': [left, right]})
    add('BPE_shared_boundary', all(x['tokens'][0][1] > x['tokens'][1][0] for x in got), got)
    e = make_synthetic('adjacent_chunks_shared_BPE_episode', renderer);
    r = measure_episode(ROOT, e, renderer);
    got = []
    for t in TEMPLATES:
        item = e.runs[0]['measurements']['benign'][t]['final'][0];
        g = r['runs'][0]['proofs']['benign'][t][0]['G'];
        chunks = g['native_chunk_locations'];
        left = token_span(chunks[0]['chars'], item['offsets']);
        right = token_span(chunks[1]['chars'], item['offsets'])
        got.append({'template': t, 'classification': r['classification'][t], 'actual_adjacent_chunks': chunks[:2],
                    'token_spans': [left, right], 'shared_real_BPE_token': left[1] > right[0]})
    add('BPE_shared_boundary_complete_episode',
        all(x['shared_real_BPE_token'] and x['classification']['class'] == 'full_collapse' for x in got), got)

    # Frozen source identity, including the existing g0-1 condition, is checked by AST.
    old = ast.parse((ROOT / 'sources/pipeline_8ee30445.txt').read_text(encoding='utf8'));
    core = ast.parse((ROOT / 'frozen_core.py').read_text(encoding='utf8'));
    v5 = ast.parse((ROOT / 'sources/pipeline_4aeb9964.txt').read_text(encoding='utf8'))
    fn = lambda tree: {x.name: ast.dump(x, include_attributes=False) for x in tree.body if
                       isinstance(x, ast.FunctionDef)}
    a, b, c = fn(old), fn(core), fn(v5);
    names = ['locate', 'token_lcp', 'classify', 'pair_integrity', 'repeat_invariant']
    add('frozen_AST_and_g0_minus_1_retained', all(a[n] == b[n] == c[n] for n in names),
        {n: hashlib.sha256(b[n].encode()).hexdigest() for n in names})
    e = make_synthetic('LCP_in_proven_glue', renderer);
    r = measure_episode(ROOT, e, renderer);
    got = []
    for t in TEMPLATES:
        cls = r['classification'][t];
        bg = r['runs'][0]['proofs']['benign'][t][0]['G'];
        ag = r['runs'][0]['proofs']['attacked'][t][0]['G'];
        item = e.runs[0]['measurements']['benign'][t]['final'][0]
        la = cls['L_attacked'];
        off = item['offsets'][la] if la < len(item['offsets']) else None
        # LCP token boundary lies in a source-proved physical gap.
        got.append({'template': t, 'classification': cls, 'G': bg, 'attacked_G': ag, 'L_attacked_offset': off,
                    'LCP_in_gap': bool(
                        off and any(g['chars'][0] <= off[0] < g['chars'][1] for g in bg.get('gaps', [])))})
    add('LCP_in_proven_glue', all(x['LCP_in_gap'] and x['classification']['class'] == 'full_collapse' for x in got),
        got)

    e = make_synthetic('empty_join_G_before_S', renderer);
    binding_rows = []
    for t in TEMPLATES:
        original = e.runs[0]['measurements']['benign'][t]['final'][0];
        request = e.runs[0]['trace']['benign']['requests'][0]
        for name in ('messages_last_request', 'raw_body_payload', 'rendered_outside_G_S', 'token_ids_same_length',
                     'offsets_legal_range', 'wrong_template'):
            item = copy.deepcopy(original);
            req = copy.deepcopy(request);
            template = t
            if name == 'messages_last_request': item['messages'][-1]['content'] += 'different'
            if name == 'raw_body_payload': req['raw_body_text'] = '{}'
            if name == 'rendered_outside_G_S': item['rendered'] = 'X' + item['rendered'][1:]
            if name == 'token_ids_same_length': item['token_ids'][0] += 1
            if name == 'offsets_legal_range': item['offsets'][1] = [0, 1]
            if name == 'wrong_template': template = 'qwen3' if t == 'llama33' else 'llama33'
            reason = renderer.binding_error(item, req, template);
            binding_rows.append({'template': t, 'mutation': name, 'rejected': reason is not None, 'reason': reason})
    add('full_binding_layer_direct_negative_controls', all(x['rejected'] for x in binding_rows), binding_rows)
    e = make_synthetic('G_S_shared_BPE_rejected_episode', renderer);
    r = measure_episode(ROOT, e, renderer);
    got = []
    for t in TEMPLATES:
        result = r['runs'][0]['proofs']['benign'][t][0];
        g = result['G'];
        s = result['S'];
        got.append({'template': t, 'G': g, 'S': s, 'classifier': r['classification'][t]})
    add('G_S_shared_BPE_rejected_complete_episode',
        all(x['G'].get('reason') == 'G_S_token_overlap' and x['classifier']['class'] == 'ambiguous' for x in got), got)
    dump(ROOT / 'results/probe_results.json', rows)
    for r in rows: print('PROBE', r['id'], 'PASS' if r['passed'] else 'FAIL', flush=True)
    return rows


def main():
    parser = argparse.ArgumentParser();
    parser.add_argument('--seal', action='store_true');
    args = parser.parse_args()
    if args.seal: seal();return
    renderer = Renderer();
    out = {}
    for name, fn in [('synthetic', run_cases), ('controls', run_controls), ('gates', gate_tests), ('probes', probes)]:
        out[name] = fn(renderer)
    counts = {k: {'total': len(v), 'passed': sum(x['passed'] for x in v),
                  'failed': [x['id'] if 'id' in x else x['variant'] + '/' + x['control'] for x in v if not x['passed']]}
              for k, v in out.items()}
    summary = {'created_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
               'runtime_manifest_sha256': sha(ROOT / 'config/runtime_manifest.json'), 'counts': counts,
               'all_passed': all(x['passed'] == x['total'] for x in counts.values()), 'real_archive_classifications': 0,
               'framework_runs': 0, 'stub_runs': 0, 'retrieval_runs': 0, 'GPU_runs': 0,
               'old_48_expectations_sha256': sha(ROOT / 'config/synthetic_expected.json')}
    dump(ROOT / 'results/test_summary.json', summary);
    print(json.dumps(summary, ensure_ascii=True, indent=2), flush=True)
    return 0 if summary['all_passed'] else 1


if __name__ == '__main__': sys.exit(main())
