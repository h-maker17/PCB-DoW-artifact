"""Archive extension identity checks; baseline instrument files remain untouched."""
from pathlib import Path
import json,hashlib,subprocess,sys
ROOT=Path(__file__).resolve().parents[1]
EXT=ROOT/'archive_extension'
class ArchiveGateClosed(RuntimeError):pass
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def objhash(x):return hashlib.sha256(json.dumps(x,ensure_ascii=True,separators=(',',':')).encode()).hexdigest()
def read_json(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def write_json(p,x):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,ensure_ascii=True,indent=2)+'\n',encoding='utf8')
def local_file(rel):
 p=(ROOT/rel).resolve()
 if not p.is_relative_to(ROOT):raise ArchiveGateClosed('file_outside_recovery')
 return p
def verify_extension():
 from render_binding import verify_runtime
 verify_runtime()
 m=read_json(EXT/'runtime_manifest.json')
 if sys.version!=m['python']:raise ArchiveGateClosed('interpreter_version_mismatch')
 for rel,h in m['files'].items():
  if sha(local_file(rel))!=h:raise ArchiveGateClosed('archive_runtime_integrity:'+rel)
 return sha(EXT/'runtime_manifest.json')
def verify_file(rec):
 p=local_file(rec['copy_file'])
 if sha(p)!=rec['sha256'] or p.stat().st_size!=rec['bytes']:raise ArchiveGateClosed('archive_input_SHA:'+rec['copy_file'])
 return p

def exact_manifest(m):
 expected={'langchain_original_main':('main',2),'langchain_v5_replication':('replication',2),'openwebui_main':('bounded_recheck',2),'autogen_main':('bounded_recheck',2),'crewai_main':('bounded_recheck',2),'mem0_diagnostic':('diagnostic',2),'deerflow_diagnostic':('diagnostic',1)}
 if {d['id'] for d in m['datasets']}!=set(expected) or len(m['datasets'])!=7:raise ArchiveGateClosed('exact_seven_datasets_required')
 all_paths=[]
 for d in m['datasets']:
  role,n=expected[d['id']]
  if d['role']!=role or d['expected_repeats']!=n or len(d['files'])!=n:raise ArchiveGateClosed('dataset_role_repeat_mismatch')
  if [r['repeat'] for r in d['files']]!=list(range(1,n+1)):raise ArchiveGateClosed('archive_repeat_identity_mismatch')
  for rec in d['files']:
   if rec['dataset']!=d['id'] or rec['role']!=role:raise ArchiveGateClosed('archive_file_dataset_role_mismatch')
   all_paths.append(rec['original_file'])
 if len(all_paths)!=13 or len(set(all_paths))!=13:raise ArchiveGateClosed('exact_13_unique_archive_files_required')
 return m

def require_admission():
 verify_extension();manifest=exact_manifest(read_json(EXT/'archive_manifest.json'));p=Path(manifest['admission_file'])
 if not p.is_file():raise ArchiveGateClosed('parent_archive_admission_missing')
 a=read_json(p)
 if a.get('decision')!='APPROVED_REAL_ARCHIVE_RECLASSIFICATION' or a.get('authority')!='parent_independent_review':raise ArchiveGateClosed('untrusted_admission_record')
 report=Path(a.get('review_report_file','')).resolve()
 if report.parent!=p.parent.resolve() or not report.is_file() or sha(report)!=a.get('review_report_sha256'):raise ArchiveGateClosed('admission_review_report_binding')
 if 'APPROVED_REAL_ARCHIVE_RECLASSIFICATION' not in report.read_text(encoding='utf8'):raise ArchiveGateClosed('review_report_has_no_execution_admission')
 head=subprocess.check_output(['git','--no-optional-locks','--git-dir='+str(ROOT/'.git'),'--work-tree='+str(ROOT),'rev-parse','HEAD'],text=True).strip()
 if a.get('approved_commit')!=head or a.get('runtime_manifest_sha256')!=sha(EXT/'runtime_manifest.json') or a.get('archive_manifest_sha256')!=sha(EXT/'archive_manifest.json') or a.get('source_registry_sha256')!=sha(EXT/'source_registry.json'):raise ArchiveGateClosed('admission_exact_packet_mismatch')
 if a.get('dataset_roles')!={d['id']:d['role'] for d in manifest['datasets']}:raise ArchiveGateClosed('admission_dataset_role_mismatch')
 return {'admission_file':str(p),'admission_sha256':sha(p),'review_report_sha256':a['review_report_sha256'],'approved_commit':head}
