"""Fresh-process fixed archive runner. Default prepares bindings; execute requires parent admission."""
from pathlib import Path
import sys,os,argparse,json
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
os.environ.update(PYTHONDONTWRITEBYTECODE='1',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',TOKENIZERS_PARALLELISM='false',HF_HOME=str(ROOT/'archive_extension/cache/hf'))
from archive_extension.integrity import EXT,read_json,write_json,verify_extension,exact_manifest,require_admission,ArchiveGateClosed
from archive_extension.loader import load_archive
from archive_extension.entry import measure_archive_episode
from archive_extension.reporting import compact,reports
from render_binding import Renderer

def main():
 p=argparse.ArgumentParser();p.add_argument('--prepare',action='store_true');p.add_argument('--execute',action='store_true');p.add_argument('--output-name',default='admitted_results');args=p.parse_args()
 if args.prepare==args.execute:raise SystemExit('Choose exactly --prepare or --execute; no arbitrary input paths.')
 if not args.output_name or Path(args.output_name).name!=args.output_name:raise SystemExit('Output must be one directory name inside archive_extension.')
 verify_extension()
 admission=require_admission() if args.execute else None
 manifest=exact_manifest(read_json(EXT/'archive_manifest.json'));renderer=Renderer();rows=[]
 for dataset in manifest['datasets']:
  print(('EXECUTE' if args.execute else 'PREPARE'),dataset['id'],dataset['role'],flush=True)
  try:
   episode=load_archive(dataset['id'],dataset['role'],renderer)
   if args.prepare:result={'status':'BINDING_PREPARED_ONLY','classification':None,'observer_calls':0,'actual_repeats':len(episode.runs),'new_LCP_computations':0,'new_classify_computations':0}
   else:result=measure_archive_episode(episode,renderer)
   rows.append(compact(episode,result))
  except (ArchiveGateClosed,RuntimeError,ValueError,KeyError,TypeError,OSError) as exc:
   rows.append({'dataset':dataset['id'],'role':dataset['role'],'scientific_eligible':dataset['scientific_eligible'],'input_records':dataset['files'],'preparation':None,'result':{'status':'invalid_preparation','classification':None,'observer_calls':0,'reason':str(exc)},'old_recorded_classifications':[None],'locations_glue_encodings_and_proofs':[]})
   print('PREPARATION_FAILED',dataset['id'],str(exc),flush=True)
 output=EXT/args.output_name
 if output.exists():raise SystemExit('Output directory already exists; choose a fresh name.')
 reports(output,rows,'ADMITTED_ARCHIVE_RECLASSIFICATION' if args.execute else 'PREPARATION_NO_MEASUREMENT')
 write_json(output/'execution_receipt.json',{'admission':admission,'actual_archive_measurement_executed':args.execute,'datasets':len(rows),'locator_calls':sum(x['result']['observer_calls'] for x in rows),'LCP_and_classify_called_on_real_data':args.execute and any(x['result']['status']=='measured' for x in rows),'framework_runs':0,'stub_runs':0,'retrieval_runs':0,'GPU_runs':0})
 print('Completed',output,flush=True)
 return 1 if any(x['result']['status']=='invalid_preparation' for x in rows) else 0
if __name__=='__main__':
 try:sys.exit(main())
 except ArchiveGateClosed as exc:raise SystemExit('GATE_CLOSED: '+str(exc))
