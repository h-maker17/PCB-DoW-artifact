"""Read-only complete repository metadata for the frozen discovery universe.

Uses shallow blobless Git metadata when REST tree evidence is unavailable.
No checkout or source execution; no eligibility inference from keyword misses.
"""
import concurrent.futures
import datetime
import hashlib
import json
import re
import subprocess
from pathlib import Path

import acquire as a


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def job(row):
    repo = row['full_name']
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise ValueError('Invalid repository identity')
    key = repo.replace('/', '__')
    base = a.ROOT / 'evidence/universe_sources' / key
    target = base / 'metadata.json'
    if target.exists():
        prior = json.loads(target.read_bytes())
        if prior.get('status') == 'archived':
            return prior
    result = {'repo': repo, 'stars': row['stargazers_count'],
              'retrieved_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'eligibility': 'pending; file-tree keywords are leads only', 'selection': False}
    try:
        official = a.ROOT / 'evidence/official' / key
        if (official / 'tree.json').exists() and (official / 'commit.json').exists():
            result['commit'] = json.loads((official / 'commit.json').read_bytes())['sha']
            tree = json.loads((official / 'tree.json').read_bytes())
            result['tree'] = tree['tree']
            result['tree_truncated'] = tree.get('truncated', False)
            result['acquisition'] = 'previously archived official REST tree at pinned commit'
        else:
            cache = a.ROOT / 'cache/repo_metadata' / key
            cache.parent.mkdir(parents=True, exist_ok=True)
            if not cache.exists():
                proc = subprocess.run(['git', '-c', 'http.proxy=http://127.0.0.1:10809',
                    '-c', 'http.version=HTTP/1.1', 'clone', '--filter=blob:none', '--no-checkout',
                    '--depth=1', f'https://github.com/{repo}.git', str(cache)],
                    capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120)
                if proc.returncode:
                    raise RuntimeError(proc.stderr[-2500:])
            result['commit'] = subprocess.check_output(['git', '-C', str(cache), 'rev-parse', 'HEAD'], text=True).strip()
            raw = subprocess.check_output(['git', '-c', 'core.quotepath=false', '-C', str(cache),
                                           'ls-tree', '-r', '--full-tree', 'HEAD'])
            result['raw_tree_sha256'] = hashlib.sha256(raw).hexdigest()
            result['tree'] = []
            for line in raw.decode('utf-8', errors='replace').splitlines():
                mode_type_sha, path = line.split('\t', 1)
                mode, kind, sha = mode_type_sha.split(' ', 2)
                result['tree'].append({'path': path, 'mode': mode, 'type': kind, 'sha': sha})
            result['tree_truncated'] = False
            result['acquisition'] = 'shallow blobless Git metadata; no checkout/source execution'
        result['status'] = 'archived'
        result['root_readmes'] = [x['path'] for x in result['tree'] if x['type'] == 'blob'
                                  and '/' not in x['path'] and re.match(r'^readme', x['path'], re.I)]
        result['rag_source_leads'] = [x['path'] for x in result['tree'] if x['type'] == 'blob'
            and x['path'].endswith(('.md', '.mdx', '.py', '.ipynb', '.json', '.yaml', '.yml'))
            and re.search(r'(^|[/_. -])(rag|retriev\w*|knowledge\w*)([/_. -]|$)', x['path'], re.I)]
    except Exception as exc:
        result['status'] = 'unavailable'
        result['error'] = str(exc)
    write(target, result)
    # Keep full trees per repository rather than duplicating them in the ledger.
    return {k: v for k, v in result.items() if k != 'tree'}


def main():
    rows = json.loads((a.ROOT / 'evidence/discovery/pool_raw.json').read_bytes())
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        for result in executor.map(job, rows):
            results.append({k: v for k, v in result.items() if k != 'tree'})
            if len(results) % 20 == 0:
                print('Metadata', len(results), '/', len(rows), 'unavailable',
                      sum(r['status'] != 'archived' for r in results), flush=True)
    write(a.ROOT / 'evidence/universe_sources/index.json',
          {'not_an_eligibility_registry': True, 'repositories': results,
           'total': len(rows), 'archived': sum(r['status'] == 'archived' for r in results)})
    print('UNIVERSE_METADATA', len(results), 'archived', sum(r['status'] == 'archived' for r in results), flush=True)


if __name__ == '__main__':
    main()
