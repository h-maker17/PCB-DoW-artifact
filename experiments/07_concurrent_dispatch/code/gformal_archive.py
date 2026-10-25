"""Freeze independent formal raw/source; rebuild only after validity review."""
import pathlib, json, sys, zipfile, hashlib, shutil
from formal_core import read, save, sha, canonical, bounded, Ledger, analysis, verify_source


def archive(root, rev, dest):
    root = pathlib.Path(root);
    rev = pathlib.Path(rev);
    dest = pathlib.Path(dest)
    if dest.exists() or dest.resolve().is_relative_to(root.resolve()): raise ValueError(
        'Archive must be new and outside raw tree')
    source = verify_source(rev);
    m = read(rev / 'experiment-manifest.json');
    ledger = Ledger(root, m['experiment_id'], read(rev / 'schedule.json'), source);
    ledger.selected()
    files = []
    for p in sorted(root.rglob('*')):
        if p.is_file(): bounded(root, p);files.append((p, 'raw/' + p.relative_to(root).as_posix()))
    # Exact frozen inputs only: exclude development history, test outputs and archive itself.
    names = list(read(rev / 'source-seal.json')['source_files']) + ['source-seal.json', 'experiment-manifest.json',
                                                                    'schedule.json', 'server-config.json',
                                                                    'client-config.json', 'protocol.md',
                                                                    'implementation-v5.md', 'token-audit.json',
                                                                    'reference-Llama-config.json',
                                                                    'data/documents.json', 'data/pilot-documents.json',
                                                                    'data/inputs.json', 'data/data-manifest.json'] + [
                'data/' + n for n in read(rev / 'experiment-manifest.json').get('source_snapshot_sha256', {})]
    for name in sorted(set(names)):
        p = bounded(rev, rev / name);
        files.append((p, 'frozen/' + name))
    manifest = [dict(path=name, bytes=p.stat().st_size, sha256=sha(p)) for p, name in files]
    with zipfile.ZipFile(dest, 'x', zipfile.ZIP_DEFLATED) as z:
        for p, name in files: z.write(p, name)
        z.writestr('file-manifest.json', json.dumps(manifest, indent=2))
    return dict(archive_sha256=sha(dest), files=len(manifest))


def rebuild(path, dest):
    dest = pathlib.Path(dest);
    dest.mkdir()
    with zipfile.ZipFile(path) as z:
        for info in z.infolist(): bounded(dest, dest / info.filename)
        z.extractall(dest)
    manifest = read(dest / 'file-manifest.json')
    for e in manifest:
        p = bounded(dest, dest / e['path'])
        if p.stat().st_size != e['bytes'] or sha(p) != e['sha256']: raise ValueError('Archive file bytes differ')
    rev = dest / 'frozen';
    root = dest / 'raw';
    source = verify_source(rev);
    m = read(rev / 'experiment-manifest.json');
    ledger = Ledger(root, m['experiment_id'], read(rev / 'schedule.json'), source)
    result = analysis(ledger, root / 'protocol/validity-lock.json', read(rev / 'data/documents.json'))
    from gformal_reports import reports
    rows, decisions = ledger.selected();
    result['secondary_tables'] = reports(ledger, rows, decisions, read(rev / 'data/documents.json'));
    return result


if __name__ == '__main__':
    result = rebuild(sys.argv[1], sys.argv[2]);
    save(pathlib.Path(sys.argv[2]) / 'rebuilt-analysis.json', result);
    print(json.dumps(dict(phase=result['phase'], primary=result['primary']['CAF'], counts=result['counts']), indent=2))
