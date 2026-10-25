"""Exclusive per-run/per-phase artifacts; no shared or previous evidence overwrite."""
from isolation import ROOT
import datetime, json, pathlib, re, threading, uuid


class RunArtifacts:
    def __init__(self, run_id, phase, *, artifact_root=None):
        for value in (run_id, phase):
            if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}', value) or value in (
                    '.', '..'): raise ValueError('Unsafe run/phase identifier')
        self.run_id = run_id;
        self.phase = phase;
        self.root = pathlib.Path(artifact_root or ROOT / 'runs') / run_id / phase
        self.root.mkdir(parents=True, exist_ok=False)
        self.lock = threading.RLock()
        self.write_new('run.json', {'run_id': run_id, 'phase': phase,
                                    'created_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                                    'scope': 'candidate isolated; no formal execution implied'})

    @classmethod
    def preparation(cls, label='unit'):
        return cls('prep-' + uuid.uuid4().hex, label)

    def path(self, relative):
        p = self.root / relative
        if not p.resolve().is_relative_to(self.root.resolve()): raise ValueError('Artifact outside run/phase')
        p.parent.mkdir(parents=True, exist_ok=True);
        return p

    def write_new(self, relative, obj):
        with self.path(relative).open('x', encoding='utf8') as f: json.dump(obj, f, ensure_ascii=False, indent=2,
                                                                            default=str)

    def new_log(self, label):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', label): raise ValueError('Unsafe log label')
        with self.lock:
            for i in range(10000):
                p = self.path(f'raw/{label}-{i:04d}.jsonl')
                try:
                    p.open('x', encoding='utf8').close();return p
                except FileExistsError:
                    continue
        raise RuntimeError('Log allocation exhausted')

    def append(self, path, record):
        if not pathlib.Path(path).resolve().is_relative_to(self.root.resolve()): raise ValueError(
            'Log outside run/phase')
        with self.lock, pathlib.Path(path).open('a', encoding='utf8') as f: f.write(
            json.dumps(record, ensure_ascii=False, default=str) + '\n')
