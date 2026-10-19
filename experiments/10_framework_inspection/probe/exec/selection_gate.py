"""Auditable sampling gate. Pending qualifications cannot be silently dropped."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads((ROOT / path).read_bytes())


def readiness():
    registry = read('evidence/universe_sources/candidate_registry.json')
    rows = registry['repositories']
    known = [r for r in rows if r['decision']['status'] == 'qualified']
    pending = [r for r in rows if r['decision']['status'] == 'pending']
    known.sort(key=lambda r: (-r['stars_snapshot'], r['repo']))
    bounds = []
    for minimum_rank, row in enumerate(known, 1):
        key = (-row['stars_snapshot'], row['repo'])
        threats = [r['repo'] for r in pending if (-r['stars_snapshot'], r['repo']) < key]
        bounds.append({'repo': row['repo'], 'stars_snapshot': row['stars_snapshot'],
                       'rank_min': minimum_rank, 'rank_max': minimum_rank + len(threats),
                       'higher_unresolved_qualifications': threats,
                       'not_a_selected_rank': True})
    return {'eligibility_complete': not pending, 'pending_count': len(pending),
            'verified_qualified_count': len(known), 'verified_qualified_rank_bounds': bounds,
            'can_freeze_stars_top6': not pending and len(known) >= 6,
            'can_execute_pilot': False,
            'reason': 'All pending qualifications must be resolved before full-pool freeze. Bounds are diagnostics, not a sample.'}


def frozen_targets(registry):
    return list(registry['selected'])+list(registry.get('prepared_extensions',[]))


def verify_frozen(registry, target_repo=None):
    facts = read('evidence/universe_sources/candidate_registry.json')
    protocol = read('config/discovery_protocol.json')
    assert registry.get('frozen') is True and registry.get('eligibility_complete') is True, 'Ranking/qualification is not frozen'
    assert facts['eligibility_complete'] is True, 'Source qualification ledger is incomplete'
    assert not any(r['decision']['status'] == 'pending' for r in facts['repositories']), 'Pending candidates cannot be dropped to force a ranking'
    qualified = sorted([r for r in facts['repositories'] if r['decision']['status'] == 'qualified'],
                       key=lambda r: (-r['stars_snapshot'], r['repo']))
    assert len(qualified) >= protocol['top_K'], 'Qualified pool smaller than preregistered top-K'
    expected = [r['repo'] for r in qualified]
    assert registry['ranked_eligible_repositories'] == expected, 'Frozen rank differs from complete eligible stars pool'
    assert [r['repo'] for r in registry['selected']] == expected[:protocol['top_K']], 'Top-6 membership differs from stars order'
    assert registry['extension_order'] == expected[protocol['top_K']:protocol['top_K'] + protocol['extension_max']], 'Extension order differs from preregistered order'
    assert registry['ranking_date_D'] == protocol['date_D_local'], 'Snapshot date mismatch'
    assert registry['primary_metric'] == 'GitHub stars', 'Primary metric cannot be changed'
    assert registry['robustness']['eligible_repositories'] == expected, 'PyPI robustness must use the same eligible pool'
    assert registry['robustness']['used_for_selection'] is False, 'Robustness cannot change the sample'
    prepared=registry.get('prepared_extensions',[])
    assert len(prepared)<=protocol['extension_max']
    assert [r['repo'] for r in prepared]==expected[protocol['top_K']:protocol['top_K']+len(prepared)], 'Prepared extension order differs from frozen ranking'
    checked = list(registry['selected'][:2])
    if target_repo is not None:
        targets = [r for r in frozen_targets(registry) if r['repo'] == target_repo]
        assert len(targets) == 1, 'Target must be a frozen top6 or ordered prepared extension member'
        target = targets[0]
        assert target['rank'] == expected.index(target_repo) + 1, 'Target rank mismatch'
        fact = next(r for r in qualified if r['repo'] == target_repo)
        assert target['stars_snapshot'] == fact['stars_snapshot'], 'Target stars mismatch'
        assert target['qualification']['status'] == 'qualified', 'Target qualification missing'
        assert target['framework_commit'] == fact['commit'], 'Target source commit mismatch'
        assert target['qualification'] == fact['decision'], 'Target qualification facts changed'
        for source in fact['qualification_dossier']['evidence']:
            content = (ROOT/source['file']).read_bytes()
            assert hashlib.sha256(content).hexdigest() == source['sha256'], source['file']
            for literal in source.get('required_literals', []):
                assert literal in content.decode('utf-8'), source['file']
        if target not in checked: checked.append(target)
    for row in checked:
        assert row['recipe']['step'] in ['step-1', 'step-2', 'none'], 'Missing official recipe selection'
        assert row['recipe']['frozen'] is True, 'Pilot recipe/adaptation not frozen'
        if row['recipe']['step']=='none':
            assert row['recipe'].get('rule_exhaustion_evidence'), 'Recipe exclusion requires audited step-1/step-2 exhaustion'
        else:
            assert row.get('adapter_module'), 'Runnable recipe requires a native official adapter'
            module = row['adapter_module']
            assert module.isidentifier(), 'Unsafe adapter module'
            adapter = 'exec/' + module + '.py'
            assert any(s['file'] == adapter for s in row['recipe']['evidence']), 'Adapter byte hash not frozen'
        for source in row['recipe']['evidence']:
            assert hashlib.sha256((ROOT / source['file']).read_bytes()).hexdigest() == source['sha256'], source['file']
    return True



def verify_rank6_preparation(target):
    contract=read('config/rank6_preparation.json')
    assert target['rank']==contract['rank']==6 and target['repo']==contract['repo']=='mem0ai/mem0'
    assert target['framework_commit']==contract['framework_commit']=='b7ad69afda6b6ed030347c66d48a13e4de9dec08'
    assert contract['native_N']==4 and contract['shared_memory_per_phase']==1
    assert contract['native_gather_return_exceptions'] is True
    assert contract['dimensions']=={'qdrant':384,'openai_embedding':384}
    for item in contract['evidence']:
        assert hashlib.sha256((ROOT/item['file']).read_bytes()).hexdigest()==item['sha256'],item['file']
    return contract


def verify_rank7_preparation(target):
    contract=read('config/rank7_preparation.json')
    assert target['rank']==contract['rank']==7 and target['repo']==contract['repo']=='microsoft/autogen'
    assert target['framework_commit']==contract['framework_commit']=='027ecf0a379bcc1d09956d46d12d44a3ad9cee14'
    assert contract['native_N']==4 and contract['original_factory_preserved'] is True
    assert contract['raw_native_ids_scores_metadata_order_unchanged'] is True
    assert contract['formal_extension_triggered_by_this_config'] is False
    for item in contract['evidence']:
        assert hashlib.sha256((ROOT/item['file']).read_bytes()).hexdigest()==item['sha256'],item['file']
    return contract


def verify_rank7_packet_reference(review,code_commit,trigger_ref):
    """Packet integrity only; this function does not grant approval."""
    name=review.get('pre_run_packet_file');expected=review.get('pre_run_packet_sha256')
    assert isinstance(name,str) and name and not Path(name).is_absolute(), 'Exact approved packet file reference missing'
    file=(ROOT/name).resolve()
    assert file.is_relative_to((ROOT/'reports').resolve()) and file.name=='pre_run_packet.json', 'Approved packet must be a project report packet'
    assert isinstance(expected,str) and len(expected)==64 and file.is_file(), 'Exact approved packet SHA/file missing'
    assert hashlib.sha256(file.read_bytes()).hexdigest()==expected, 'Approved packet SHA does not match actual file'
    packet=json.loads(file.read_bytes())
    assert packet['rank']==7 and packet['repo']=='microsoft/autogen' and packet['code_commit']==code_commit, 'Approved packet identity differs from exact formal target'
    assert packet['accepted_top6_extension_trigger']==trigger_ref, 'Approved packet not bound to accepted extension trigger'
    return {'file':name,'sha256':expected}


def verify_rank7_extension(registry,authorization,code_commit):
    verify_frozen(registry,'microsoft/autogen')
    target=next(x for x in frozen_targets(registry) if x['repo']=='microsoft/autogen')
    contract=verify_rank7_preparation(target)
    trigger_path=ROOT/contract['trigger_file']
    assert trigger_path.is_file(), 'Top6 independent acceptance/extension trigger missing'
    trigger=read(contract['trigger_file'])
    assert trigger['approved'] is True and trigger['parent_thread']==authorization['parent_thread']==contract['parent_thread']
    assert trigger['next_rank']==7 and trigger['repo']=='microsoft/autogen'
    assert trigger['fixed_extension_order']==registry['extension_order'] and trigger['maximum_extensions']==4
    assert hashlib.sha256(trigger_path.read_bytes()).hexdigest()==contract['accepted_extension_trigger']['sha256'], 'Extension trigger differs from frozen accepted record'
    accepted_ref=trigger['accepted_matrix_snapshot']
    assert hashlib.sha256((ROOT/accepted_ref['file']).read_bytes()).hexdigest()==accepted_ref['sha256']
    accepted=read(accepted_ref['file'])
    assert accepted['all6_parent_accepted'] is True and all(x['parent_accepted'] is True for x in accepted['matrix'])
    assert trigger['all6_actual_terminals_accepted'] is True and trigger['fullcollapse_count']==0
    for key,item in contract['top6_prerequisite'].items():
        assert trigger[key]==item, 'Wrong exact top6 predecessor evidence'
        assert hashlib.sha256((ROOT/item['file']).read_bytes()).hexdigest()==item['sha256']
    cp=read(trigger['rank6_checkpoint']['file']);audit=read(trigger['rank6_audit']['file']);matrix=read(trigger['top6_matrix']['file'])
    assert cp['current_rank']==audit['rank']==6 and cp['attempt_id']==audit['attempt_id']=='run_20261011T213437684403Z'
    assert cp['code_commit']==audit['code']['commit']=='6ab6fb5b09d895f2c6d9055f334d1963e0bba084'
    assert cp['audit_sha256']==trigger['rank6_audit']['sha256'] and audit['native_add_completions']==16
    item=trigger['rank6_parent_acceptance'];assert hashlib.sha256((ROOT/item['file']).read_bytes()).hexdigest()==item['sha256']
    parent=read(item['file'])
    assert parent['approved'] is True and parent['parent_thread']==contract['parent_thread']
    assert parent['rank']==6 and parent['repo']=='mem0ai/mem0' and parent['attempt_id']==cp['attempt_id']
    assert parent['code_commit']==cp['code_commit'] and parent['checkpoint']==trigger['rank6_checkpoint'] and parent['audit']==trigger['rank6_audit']
    assert parent['accepted_top6_actual_terminals'] is True
    rows=matrix['matrix'];assert [x['rank'] for x in rows]==list(range(1,7))
    assert len(accepted['matrix'])==6
    for original,accepted_row in zip(rows,accepted['matrix']):
        assert {k:v for k,v in original.items() if k not in ['parent_accepted','parent_acceptance']}=={k:v for k,v in accepted_row.items() if k not in ['parent_accepted','parent_acceptance']}, 'Accepted snapshot rewrites actual terminal facts' 
    assert [x['repo'] for x in rows]==[x['repo'] for x in registry['selected']]
    assert all(x['parent_accepted'] is True for x in rows[:5])
    assert rows[5]['attempt_id']==cp['attempt_id'] and rows[5]['classification']==audit['classification']
    fullcollapse=sum(any(v.get('class')=='full_collapse' for v in (x['classification'] or {}).values()) for x in rows)
    assert fullcollapse==trigger['fullcollapse_count']==0, 'Fullcollapse exists; fixed extension must not trigger'
    verify_predecessor_terminal(authorization,4,'bytedance/deer-flow')
    verify_predecessor_terminal(authorization,5,'FoundationAgents/MetaGPT')
    from native_bridge import verify_runtime
    verify_runtime('ag')
    if code_commit is not None:
        review=read('reports/continuation/pre_run_approval.json')
        assert review['approved'] is True and review['code_commit']==code_commit
        assert review['parent_thread']==contract['parent_thread'] and review['rank']==7 and review['repo']=='microsoft/autogen', 'Exact rank7 approval missing'
        assert review['extension_trigger_sha256']==hashlib.sha256(trigger_path.read_bytes()).hexdigest(), 'Exact approval not bound to accepted extension trigger'
        verify_rank7_packet_reference(review,code_commit,contract['accepted_extension_trigger'])
    return {**authorization,'next_rank':7,'authorized_framework':'microsoft/autogen','user_authorization':contract['technical_authorization'],
            'extension_trigger':{'file':contract['trigger_file'],'sha256':hashlib.sha256(trigger_path.read_bytes()).hexdigest()}}


def verify_rank8_preparation(target):
    contract=read('config/rank8_preparation.json')
    assert target['rank']==contract['rank']==8 and target['repo']==contract['repo']=='crewAIInc/crewAI'
    assert target['framework_commit']==contract['framework_commit']=='6b93fa0e25a926215ca7bb52a95d4db7d9d7262d'
    assert contract['native_N']==4 and contract['preparation_only'] is True
    assert contract['native_body_defaults_preserved'] and contract['raw_native_IDs_scores_metadata_preserved']
    for item in contract['evidence']:assert hashlib.sha256((ROOT/item['file']).read_bytes()).hexdigest()==item['sha256'],item['file']
    return contract


def verify_rank8_extension(registry,authorization,code_commit):
    # First preserve the exact historical top6 fixed extension trigger.
    verify_rank7_extension(registry,authorization,None)
    verify_frozen(registry,'crewAIInc/crewAI')
    target=next(x for x in frozen_targets(registry) if x['repo']=='crewAIInc/crewAI')
    contract=verify_rank8_preparation(target)
    refs=contract['rank7_terminal']
    for item in refs.values():assert hashlib.sha256((ROOT/item['file']).read_bytes()).hexdigest()==item['sha256']
    cp=read(refs['checkpoint']['file']);audit=read(refs['audit']['file']);matrix=read(refs['matrix']['file'])
    assert cp['current_rank']==audit['rank']==7 and cp['attempt_id']==audit['attempt_id']==contract['rank7_attempt_id']
    assert cp['code_commit']==audit['code']['commit']==contract['rank7_code_commit'] and cp['audit_sha256']==refs['audit']['sha256']
    accepted_file=ROOT/contract['rank7_parent_acceptance_file']
    assert accepted_file.is_file(), 'Actual rank7 terminal acceptance missing; formal8 closed'
    accepted=read(contract['rank7_parent_acceptance_file'])
    assert accepted['approved'] is True and accepted['parent_thread']==contract['parent_thread']
    assert accepted['rank']==7 and accepted['repo']=='microsoft/autogen' and accepted['code_commit']==cp['code_commit'] and accepted['attempt_id']==cp['attempt_id']
    assert accepted['checkpoint']==refs['checkpoint'] and accepted['audit']==refs['audit'] and accepted['classification']==audit['classification']
    assert matrix['matrix'][6]['classification']==audit['classification'] and matrix['matrix'][6]['attempt_id']==audit['attempt_id']
    assert not any(v.get('class')=='full_collapse' for v in (audit['classification'] or {}).values()), 'Valid fullcollapse at7; extension must stop'
    from native_bridge import verify_runtime
    verify_runtime('ca')
    acceptance_ref={'file':contract['rank7_parent_acceptance_file'],'sha256':hashlib.sha256(accepted_file.read_bytes()).hexdigest()}
    if code_commit is not None:
        review=read('reports/continuation/pre_run_approval.json')
        assert review['approved'] is True and review['rank']==8 and review['repo']=='crewAIInc/crewAI' and review['code_commit']==code_commit and review['parent_thread']==contract['parent_thread'], 'Exact real rank8 approval missing'
        assert review['extension_trigger_sha256']==contract['accepted_extension_trigger']['sha256'] and review['rank7_acceptance_sha256']==acceptance_ref['sha256']
        name=review.get('pre_run_packet_file');expected=review.get('pre_run_packet_sha256')
        assert isinstance(name,str) and not Path(name).is_absolute(), 'Exact approved rank8 packet reference missing'
        file=(ROOT/name).resolve();assert file.is_relative_to((ROOT/'reports').resolve()) and file.name=='pre_run_packet.json' and file.is_file()
        assert hashlib.sha256(file.read_bytes()).hexdigest()==expected, 'Approved rank8 packet SHA differs from actual file'
        packet=read(name);assert packet['rank']==8 and packet['repo']=='crewAIInc/crewAI' and packet['code_commit']==code_commit
        assert packet['accepted_top6_extension_trigger']==contract['accepted_extension_trigger'] and packet['accepted_rank7_terminal']==acceptance_ref
    return {**authorization,'next_rank':8,'authorized_framework':'crewAIInc/crewAI','user_authorization':contract['technical_authorization'],'rank7_parent_acceptance':acceptance_ref}


def verify_predecessor_terminal(authorization, rank, repo):
    prefix='rank'+str(rank)
    for suffix in ['checkpoint','audit','parent_terminal_acceptance']:
        item=authorization.get(prefix+'_'+suffix)
        assert item and hashlib.sha256((ROOT/item['file']).read_bytes()).hexdigest()==item['sha256'], 'Missing audited accepted predecessor terminal: '+prefix
    cp=read(authorization[prefix+'_checkpoint']['file'])
    audit=read(authorization[prefix+'_audit']['file'])
    parent=read(authorization[prefix+'_parent_terminal_acceptance']['file'])
    assert cp['current_rank']==parent['rank']==rank and parent['repo']==repo
    assert parent['approved'] is True and parent['parent_thread']==authorization['parent_thread']
    assert parent['code_commit']==cp['code_commit']==audit['code']['commit']
    assert parent['checkpoint']==authorization[prefix+'_checkpoint'] and parent['audit']==authorization[prefix+'_audit']
    assert cp['ledger']['rank']==rank and cp['ledger']['result']['repo']==repo
    assert cp['ledger']['result']['code_commit']==parent['code_commit']
    return parent


def verify_continuation(registry, target_repo, code_commit=None):
    authorization = read('config/continuation_authorization.json')
    assert authorization['pilot_parent_factual_review_passed'] is True, 'Pilot review not approved'
    if target_repo=='microsoft/autogen':
        return verify_rank7_extension(registry,authorization,code_commit)
    if target_repo=='crewAIInc/crewAI':
        return verify_rank8_extension(registry,authorization,code_commit)
    assert target_repo == authorization['authorized_framework'], 'Continuation target not approved' 
    target = next(r for r in registry['selected'] if r['repo'] == target_repo)
    assert target['rank'] == authorization['next_rank'], 'Cannot skip frozen rank order'
    for name in ['user_authorization', 'pilot_checkpoint', 'pilot_audit']:
        item = authorization[name]
        assert hashlib.sha256((ROOT/item['file']).read_bytes()).hexdigest() == item['sha256'], name
    checkpoint = read(authorization['pilot_checkpoint']['file'])
    assert checkpoint['ranks'] == [1, 2], 'Wrong reviewed checkpoint'
    assert checkpoint['results'][1]['status'] == 'executed_reproducible', 'OW pilot not completed'
    assert checkpoint['langflow_exclusion_scope'].startswith('Only frozen step-2 MergeFlows'), 'LF exclusion scope changed'
    assert target['recipe']['execution_authorized_in_current_phase'] is True
    verify_frozen(registry, target_repo)
    if target['rank']==4:
        assert target_repo=='bytedance/deer-flow' and target['framework_commit']=='cd156872846d74536dbac78e12ec695399ead4c5', 'Fixed rank4 identity changed'
        for name in ['rank3_checkpoint','rank3_audit','rank3_parent_factual_review']:
            item=authorization[name]
            assert hashlib.sha256((ROOT/item['file']).read_bytes()).hexdigest()==item['sha256'], name
        cp=read(authorization['rank3_checkpoint']['file']);audit=read(authorization['rank3_audit']['file']);parent=read(authorization['rank3_parent_factual_review']['file'])
        assert cp['current_rank']==3 and cp['next_rank']==4, 'Wrong predecessor checkpoint'
        result=cp['rank3_result']['result']
        assert result['repo']=='langchain-ai/langchain' and result['status']=='executed_reproducible', 'Rank3 not completed'
        assert result['code_commit']==audit['code']['commit']==parent['code_commit']=='8ee30445f7d3b431ec32ae26dcfad7aae9a2bdaf', 'Rank3 audited commit mismatch'
        assert cp['rank3_audit_sha256']==authorization['rank3_audit']['sha256'] and audit['status']=='executed_reproducible', 'Rank3 audit mismatch'
        assert parent['approved'] is True and parent['rank']==3 and parent['repo']=='langchain-ai/langchain' and parent['parent_thread']==authorization['parent_thread'], 'Rank3 parent factual review missing'
        assert cp['rank3_ambiguous_not_counted_as_negative_immunity_or_mission'] is True, 'Rank3 disposition changed'
        assert authorization['maximum_cumulative_seconds']==28800, 'Fixed rank4 budget changed'
        from native_bridge import verify_runtime
        verify_runtime('df')
    if target['rank']==5:
        assert target_repo=='FoundationAgents/MetaGPT', 'Fixed rank5 identity changed'
        parent=verify_predecessor_terminal(authorization,4,'bytedance/deer-flow')
        assert parent['classification'] is None and parent['actual_repeats_started']==1
        assert parent['not_negative_immune_positive_reproducible_or_framework_incapacity'] is True
    if target['rank']==6:
        contract=verify_rank6_preparation(target)
        parent=verify_predecessor_terminal(authorization,5,'FoundationAgents/MetaGPT')
        cp=read(authorization['rank5_checkpoint']['file']);audit=read(authorization['rank5_audit']['file'])
        assert parent['attempt_id']==cp['attempt_id']=='run_20261011T203814450027Z', 'Wrong actual rank5 terminal attempt'
        assert parent['code_commit']=='52c8a406b5cf8ddc7cbf7d61c200669479dfed5b', 'Wrong accepted predecessor instrument'
        assert parent['classification'] is None and parent['actual_main_body_invocations']==1
        assert parent['native_first_publish_failure_confirmed'] is True and parent['rank6_ordered_preparation_allowed'] is True
        assert parent['old_two_instrument_failures_counted_in_budget_not_framework_exclusions'] is True
        assert parent['not_immune_negative_positive_fullcollapse_or_reproducible'] is True
        assert cp['audit_sha256']==authorization['rank5_audit']['sha256']
        assert cp['native_first_publish_failure_confirmed'] is True and cp['actual_main_body_invocations']==1
        assert audit['actual_original_main_body_invocations']==1 and audit['native_primitive_first_publish_failure_confirmed'] is True
        assert audit['models']==audit['retrievals']==0 and audit['classification'] is None
        expected={name:authorization['rank5_'+name] for name in ['checkpoint','audit','parent_terminal_acceptance']}
        assert contract['rank5_formal_terminal']==expected, 'Rank6 contract not bound to exact accepted rank5 terminal'
        assert contract['formal_enabled'] is True and contract['rank5_formal_terminal'], 'Rank6 formal stage has not been enabled after rank5 terminal review'
        from native_bridge import verify_runtime
        verify_runtime('mm')
    if code_commit is not None:
        review = read('reports/continuation/pre_run_approval.json')
        assert review['approved'] is True, 'Pre-run parent audit missing'
        assert review['code_commit'] == code_commit, 'Pre-run audit is for a different instrument commit'
        assert review['parent_thread'] == authorization['parent_thread'], 'Wrong reviewing thread'
        assert review['rank']==target['rank'] and review['repo']==target_repo, 'Pre-run approval rank/repo mismatch'
    return authorization

if __name__ == '__main__':
    result = readiness()
    (ROOT / 'reports/rank_readiness.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: result[k] for k in ['pending_count', 'verified_qualified_count', 'can_freeze_stars_top6']}, ensure_ascii=False))
