import json, os, hashlib, urllib.request, datetime
from pathlib import Path

ROOT = Path(os.environ.get('TASKLAB_ROOT', Path(__file__).resolve().parents[2]))
MODEL = 'llama-3.3-70b'
URL = 'http://127.0.0.1:8000'
TASKS = ['hotpotqa', 'gov_report', 'triviaqa', 'synthetic']


def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat()


def save(path, value):
    path = Path(path);
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp');
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8');
    tmp.replace(path)


def read(path): return json.loads(Path(path).read_text(encoding='utf-8'))


def digest(text): return hashlib.sha256(text.encode()).hexdigest()


def api(endpoint, payload):
    req = urllib.request.Request(URL + endpoint, data=json.dumps(payload, ensure_ascii=False).encode(),
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=600) as response: return json.loads(response.read())


def tokenize(**fields): return api('/tokenize', dict(model=MODEL, **fields))


def lcp(a, b):
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y: return i
    return min(len(a), len(b))
