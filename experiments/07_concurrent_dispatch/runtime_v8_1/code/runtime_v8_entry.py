"""Explicit separately approved monitoring extension over an unchanged formal v7.

This entry never changes the v7 seal or ledger source checks. The formal
measurement bundle remains v7; runtime monitoring provenance is additionally
sealed into every segment and every monitor record, and archived with raw data.
"""
from pathlib import Path
import hashlib,json,importlib.util,sys,os

EXT=Path(__file__).resolve().parents[1]

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def canonical(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()

def verify_extension(extension,formal,approval):
    extension=Path(extension);formal=Path(formal)
    m=read(extension/'extension-manifest.json');a=read(approval);seal=read(extension/'source-seal.json')
    actual={p.relative_to(extension).as_posix():sha(p) for kind in ('code','tests') for p in sorted((extension/kind).glob('*.py'))}
    if actual!=seal['source_files'] or canonical(actual)!=seal['source_bundle_sha256']:
        raise ValueError('Execution extension source seal differs')
    if (m.get('kind')!='explicit_runtime_monitoring_extension' or
        m.get('formal_revision')!=formal.name or
        m.get('formal_manifest_sha256')!=sha(formal/'experiment-manifest.json') or
        m.get('formal_source_bundle_sha256')!=read(formal/'source-seal.json')['source_bundle_sha256'] or
        m.get('original_monitor_sha256')!=sha(formal/'code/gformal_monitor.py') or
        m.get('experiment_id')!=read(formal/'experiment-manifest.json')['experiment_id'] or
        m.get('monitor_policy_changed') is not False or m.get('automatic_retries') is not False or
        m.get('formal_raw_records_rewritten') is not False or m.get('startup_diagnostics_per_segment')!=25):
        raise ValueError('Execution extension formal identity/scope differs')
    if (a.get('execution_extension_authorized') is not True or
        a.get('extension_manifest_sha256')!=sha(extension/'extension-manifest.json') or
        a.get('extension_source_sha256')!=seal['source_bundle_sha256'] or
        not a.get('human_extension_release_reference')):
        raise ValueError('Specific execution extension has not been released')
    return m,seal,a

def formal_modules(formal):
    sys.path.insert(0,str(Path(formal)/'code'))
    import formal_core as core
    import gformal_stage as stage
    if Path(core.__file__).resolve()!= (Path(formal)/'code/formal_core.py').resolve() or Path(stage.__file__).resolve()!=(Path(formal)/'code/gformal_stage.py').resolve():
        raise ValueError('Unexpected formal module path')
    return core,stage

def copy_exact(source,destination):
    destination=Path(destination);raw=Path(source).read_bytes();destination.parent.mkdir(parents=True,exist_ok=True)
    if destination.is_symlink():raise ValueError('Extension evidence symlink')
    if destination.exists():
        if destination.read_bytes()!=raw:raise ValueError('Extension evidence bytes changed')
    else:
        with destination.open('xb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
    if os.name=='posix':
        directory=os.open(destination.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(directory)
        finally:os.close(directory)

def run(approval,segment,extension=EXT,formal=None):
    extension=Path(extension);formal=Path(formal) if formal else extension.parent/'formal-v7-20261012'
    # Both approvals and both seals are checked before any folder publication,
    # monitor substitution, stage call, service, sampler or model request.
    m,seal,a=verify_extension(extension,formal,approval)
    core,stage=formal_modules(formal)
    fm=core.gate(formal,approval)
    if core.verify_source(formal)!=m['formal_source_bundle_sha256']:
        raise ValueError('Formal source differs from extension binding')
    if not segment.startswith('formal-') or '/' in segment or '\\' in segment:
        raise ValueError('Unsafe extension segment')
    root=stage.ROOT/'formal'/fm['experiment_id'];folder=root/'segments'/segment
    if folder.exists():raise ValueError('Existing segment cannot be restarted')
    if stage.REV.resolve()!=formal.resolve():raise ValueError('Stage revision differs')
    # Recover/adjudicate first, outside this entry. Existing frozen guards still
    # reject unfinished, unauthorized retry and unresolved resource histories.
    ledger=core.Ledger(root,fm['experiment_id'],core.read(formal/'schedule.json'),m['formal_source_bundle_sha256'])
    if ledger.unfinished():raise ValueError('Unfinished attempt; no model launch')
    core.verify_resume(ledger,a.get('resume_review'))
    frozen=root/'protocol/execution-extensions'/seal['source_bundle_sha256']
    for name in sorted(set(seal['source_files'])|{'source-seal.json','extension-manifest.json'}):
        copy_exact(extension/name,frozen/name)
    copy_exact(approval,frozen/'execution-approval.json')
    provenance=dict(experiment_id=fm['experiment_id'],segment=segment,
        formal_source_bundle_sha256=m['formal_source_bundle_sha256'],
        formal_manifest_sha256=m['formal_manifest_sha256'],
        execution_extension_source_sha256=seal['source_bundle_sha256'],
        execution_extension_manifest_sha256=sha(extension/'extension-manifest.json'),
        execution_approval_sha256=sha(approval),
        archived_extension_path=frozen.relative_to(root).as_posix(),
        replacement='gformal_stage.ResourceMonitor only',
        unchanged_formal_modules={p.name:sha(p) for p in sorted((formal/'code').glob('*.py'))},
        no_formal_source_fields_relabelled=True,created_utc=core.utc())
    # The unchanged stage owns segment mkdir. Publish a durable source intent
    # outside that directory before calling stage; do not pre-create its folder.
    intent=frozen/'segment-intents'/(segment+'.json')
    core.save(intent,provenance)
    spec=importlib.util.spec_from_file_location('approved_runtime_v8_monitor',extension/'code/runtime_v8_monitor.py')
    monitor=importlib.util.module_from_spec(spec);spec.loader.exec_module(monitor)
    monitor.EXECUTION_EXTENSION_SOURCE=seal['source_bundle_sha256']
    original=stage.ResourceMonitor
    if monitor.POLICY!=core.read(formal/'experiment-manifest.json')['resource_monitor_policy']:
        raise ValueError('Extension monitor policy differs')
    stage.ResourceMonitor=monitor.ResourceMonitor
    outcome={'execution_extension_source_sha256':seal['source_bundle_sha256'],'segment':segment}
    try:
        value=stage.main(Path(approval),segment);outcome['stage_returned']=True
        return value
    except BaseException as exc:
        outcome.update(stage_returned=False,error_type=type(exc).__name__,message=str(exc))
        raise
    finally:
        stage.ResourceMonitor=original
        # Stage normally creates the folder itself. An error before its mkdir
        # still receives an audit folder only after the attempted entry returns.
        folder.mkdir(parents=True,exist_ok=True)
        copy_exact(intent,folder/'execution-extension.json')
        outcome['finished_utc']=core.utc()
        outcome['stage_evidence_sha256']={name:sha(folder/name) for name in ('execution-extension.json','cleanup-cost.json','stage-completed.json','resource-final-check.json','stage-review-pause.json') if (folder/name).is_file()}
        core.save(folder/'execution-extension-outcome.json',outcome)

def verify_archived_extension(root,segment,formal=None,expected_abort_reviews=None):
    root=Path(root);folder=root/'segments'/segment;p=read(folder/'execution-extension.json')
    relative=Path(p['archived_extension_path'])
    if relative.is_absolute() or '..' in relative.parts:raise ValueError('Unsafe archived extension path')
    ext=root/relative;seal=read(ext/'source-seal.json')
    if canonical(seal['source_files'])!=seal['source_bundle_sha256'] or seal['source_bundle_sha256']!=p['execution_extension_source_sha256']:
        raise ValueError('Archived extension seal identity differs')
    for name,digest in seal['source_files'].items():
        rel=Path(name)
        if rel.is_absolute() or '..' in rel.parts or sha(ext/rel)!=digest:raise ValueError('Archived extension file differs')
    if sha(ext/'extension-manifest.json')!=p['execution_extension_manifest_sha256'] or sha(ext/'execution-approval.json')!=p['execution_approval_sha256']:
        raise ValueError('Archived extension manifest/approval differs')
    a=read(ext/'execution-approval.json');m=read(ext/'extension-manifest.json')
    if (a.get('execution_extension_authorized') is not True or a.get('extension_source_sha256')!=seal['source_bundle_sha256'] or
        a.get('extension_manifest_sha256')!=p['execution_extension_manifest_sha256'] or not a.get('human_extension_release_reference') or
        m['formal_source_bundle_sha256']!=p['formal_source_bundle_sha256'] or m['formal_manifest_sha256']!=p['formal_manifest_sha256']):
        raise ValueError('Archived extension release differs')
    if formal is not None:
        formal=Path(formal)
        if (sha(formal/'experiment-manifest.json')!=p['formal_manifest_sha256'] or
            read(formal/'source-seal.json')['source_bundle_sha256']!=p['formal_source_bundle_sha256'] or
            {f.name:sha(f) for f in sorted((formal/'code').glob('*.py'))}!=p['unchanged_formal_modules']):
            raise ValueError('Archived extension does not bind frozen formal modules')
    outcome=read(folder/'execution-extension-outcome.json')
    if outcome['execution_extension_source_sha256']!=seal['source_bundle_sha256']:raise ValueError('Extension outcome identity differs')
    for name,digest in outcome['stage_evidence_sha256'].items():
        if Path(name).name!=name or sha(folder/name)!=digest:raise ValueError('Extension stage evidence changed')
    resource=folder/'resource-monitor.jsonl'
    if not resource.exists():
        # An already preserved predecessor can have failed before any sampler,
        # service or model request. It is accepted only with an explicitly
        # reviewed, source-bound abort proof from the signed corrective manifest.
        binding=(expected_abort_reviews or {}).get(segment)
        if not binding:raise ValueError('No extension resource records or signed abort review')
        path=Path(binding['evidence_path'])
        if path.is_absolute() or '..' in path.parts or sha(root/path)!=binding['evidence_sha256']:raise ValueError('Pre-service abort review differs')
        proof=read(root/path)
        if (proof.get('pre_service_abort_verified') is not True or proof.get('effect_inspected') is not False or
            proof.get('segment')!=segment or proof.get('execution_extension_source_sha256')!=seal['source_bundle_sha256'] or
            proof.get('extension_outcome_sha256')!=sha(folder/'execution-extension-outcome.json') or
            proof.get('segment_provenance_sha256')!=sha(folder/'execution-extension.json') or
            proof.get('actual_model_requests')!=0 or proof.get('new_formal_attempts')!=0 or outcome.get('stage_returned') is not False):
            raise ValueError('Pre-service abort identity/evidence differs')
        if any((folder/name).exists() for name in ('server.log','driver.log','owned-service.json','ready.json','completion.json','cleanup-cost.json')):
            raise ValueError('Pre-service abort has service/driver artifacts')
        log=Path(proof['stage_log_path'])
        if log.is_absolute() or '..' in log.parts or sha(root/log)!=proof['stage_log_sha256']:raise ValueError('Pre-service abort trace differs')
        return {'passed':True,'pre_service_abort_verified':True,'execution_extension_source_sha256':seal['source_bundle_sha256'],'formal_source_bundle_sha256':p['formal_source_bundle_sha256']}
    lines=resource.read_text().splitlines()
    if not lines:raise ValueError('No extension resource records')
    for line in lines:
        if read_json_line(line).get('execution_extension_source_sha256')!=seal['source_bundle_sha256']:
            raise ValueError('Resource monitor lacks extension provenance')
    return {'passed':True,'execution_extension_source_sha256':seal['source_bundle_sha256'],'formal_source_bundle_sha256':p['formal_source_bundle_sha256']}

def read_json_line(line):return json.loads(line)

if __name__=='__main__':
    print(json.dumps(run(Path(sys.argv[1]),sys.argv[2]),ensure_ascii=False,indent=2))
