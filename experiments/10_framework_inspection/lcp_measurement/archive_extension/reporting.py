"""Report archive results without changing original scientific artifacts."""
import csv,json
from archive_extension.integrity import write_json
from render_binding import TEMPLATES
from observer import physical_hits

def compact(e,result):
 locations=[]
 for repeat,run in enumerate(result.get('runs',[]),1):
  for phase,templates in run['proofs'].items():
   for t,items in templates.items():
    for replica,item in enumerate(items):locations.append({'repeat':repeat,'phase':phase,'template':t,'replica':replica,**item})
 locations+=result.get('diagnostic_locations',[])
 
 for loc in locations:
  if loc['G']['status']=='ok':continue
  run=e.runs[loc['repeat']-1];phase=loc['phase'];replica=loc['replica'];item=next(x for x in run['measurements'][phase][loc['template']]['final'] if x['replica']==replica);native=next(x for x in run['trace'][phase]['returns'] if x['replica']==replica or x['replica']=='broadcast')
  loc['diagnostic_chunk_hits']=[{'native_index':i,'physical_encoding_hits':physical_hits(item['rendered'],chunk)} for i,chunk in enumerate(native['raw_chunks'])]
 old=[r.get('classification') for r in e.runs]
 return {'dataset':e.dataset['id'],'role':e.dataset['role'],'scientific_eligible':e.dataset['scientific_eligible'],'input_records':e.records,'preparation':e.preparation,'result':{k:v for k,v in result.items() if k not in ('runs','diagnostic_locations')},'old_recorded_classifications':old,'locations_glue_encodings_and_proofs':locations}

def reports(output,rows,mode):
 output.mkdir(parents=True,exist_ok=True);write_json(output/'archive_results.json',{'mode':mode,'datasets':rows})
 summary=[];intervals=[]
 for row in rows:
  result=row['result'];classes=result.get('classification') or {}
  old=row['old_recorded_classifications'][0] or {}
  for t in TEMPLATES:
   cls=classes.get(t,{})
   summary.append({'dataset':row['dataset'],'role':row['role'],'template':t,'status':result['status'],'old_recorded_class':old.get(t,{}).get('class'),'new_scientific_class':cls.get('class'),'L_benign':cls.get('L_benign'),'L_attacked':cls.get('L_attacked'),'signed_difference':cls.get('signed_difference'),'positive_loss':cls.get('positive_loss'),'template_dependent':result.get('template_dependent',False),'actual_repeats':result.get('actual_repeats'),'pair_valid':result.get('pair_valid'),'repeat_valid':result.get('repeat_valid')})
  for loc in row['locations_glue_encodings_and_proofs']:
   g=loc['G'];s=loc['S'];intervals.append({'dataset':row['dataset'],'role':row['role'],'repeat':loc['repeat'],'phase':loc['phase'],'template':loc['template'],'replica':loc['replica'],'diagnostic_only':loc.get('diagnostic_only',False),'G_status':g['status'],'G_reason':g.get('reason'),'G_chars':json.dumps(g.get('chars')),'G_tokens':json.dumps(g.get('tokens')),'diagnostic_G_chars':json.dumps(g.get('diagnostic_envelope',{}).get('chars')),'diagnostic_G_tokens':json.dumps(g.get('diagnostic_envelope',{}).get('tokens')),'S_chars':json.dumps(s.get('chars')),'S_tokens':json.dumps(s.get('tokens')),'reordered':g.get('reordered'),'actual_gaps':json.dumps(g.get('gaps',g.get('diagnostic_envelope',{}).get('gaps')),ensure_ascii=False),'chunk_encodings':json.dumps([x['encodings'] for x in g.get('native_chunk_locations',[])],ensure_ascii=False),'diagnostic_chunk_hits':json.dumps(loc.get('diagnostic_chunk_hits'),ensure_ascii=False),'source_proof':json.dumps(loc['proof'],ensure_ascii=False)})
 for filename,data in [('old_new_table.csv',summary),('glue_encoding_coordinates.csv',intervals)]:
  with (output/filename).open('w',encoding='utf-8-sig',newline='') as f:
   if data:w=csv.DictWriter(f,fieldnames=list(data[0]));w.writeheader();w.writerows(data)
 write_json(output/'old_new_table.json',summary)
 (output/'honest_scope.txt').write_text('Uniform post-hoc observer correction; original historical No-Go retained. Original LC is main, v5 same-framework replication and does not increase independent-framework N. token-LCP measures request sequences, not cache/memory/cost/latency/quality. Complete S is not native persistent history. Step2 is not official end-to-end recipe. mem0 exact score repeat invalid and DeerFlow citation/receipt pair invalid plus missing repeat2 remain; Langflow/MetaGPT/LlamaIndex/nanobot untouched.\n',encoding='utf8')
