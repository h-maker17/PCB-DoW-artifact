"""Explicit synthetic cause/resume review fixtures, never production authority."""
from pathlib import Path
import formal_core as core
import gformal_adjudication as adjudication

def cause_review(ledger,info,reason='log_corruption'):
 p=ledger.root/'segments'/info['segment_id']/'CPU-cause-evidence.json'
 if not p.exists():core.save(p,dict(synthetic_CPU_fixture=True,reason=reason,evidence='Injected CPU interruption cause; no model request'))
 raw=ledger.path.read_bytes()
 return dict(experiment_id=ledger.experiment,source_revision=ledger.source,
  attempt_id=info['attempt_id'],ledger_prefix_bytes=len(raw),ledger_prefix_sha256=core.sha(ledger.path),
  review_reference='explicit synthetic CPU cause review',effect_inspected=False,reason=reason,
  evidence_files={p.relative_to(ledger.root).as_posix():core.sha(p)})

def reviewed_recovery(ledger,cleanup,reference):
 reviews={}
 for info in ledger.unfinished():
  p=ledger.root/'rounds'/info['attempt_id']/'recovered-cause-review.json'
  reviews[info['attempt_id']]=core.read(p) if p.exists() else cause_review(ledger,info)
 return core.recover_unfinished(ledger,cleanup,reference,reviews)

def retain_and_resume(ledger):
 current=adjudication.state(ledger,False)
 if current['unresolved']:
  segments={Path(x).parent.name for x in current['unresolved']}
  decisions={e['attempt_id']:dict(reason='retain',evidence_reference='explicit synthetic retain; effect blind') for e in ledger.events if e['event']=='finished' and e['segment_id'] in segments}
  current=adjudication.publish(ledger,ledger.root/'protocol/resource-adjudications/CPU-retain.json',decisions,'synthetic retain review before effects')
 return dict(ledger_sha256=core.sha(ledger.path),resource_state_sha256=current['binding_sha256'],review_reference='synthetic resume review')
