from pathlib import Path
import argparse,asyncio,json,sys,traceback
R=Path(__file__).resolve().parent;sys.path.insert(0,str(R/'candidate'))
from crewai_n4_candidate import execute_candidate
p=argparse.ArgumentParser();p.add_argument('--input','--native-input',dest='input',required=True);config=json.loads(Path(p.parse_args().input).read_text(encoding='utf-8'))
try:
 report=asyncio.run(execute_candidate(config));print(json.dumps({'native_recipe_completed':report['native_recipe_completed'],'branch_count':report['branch_count'],'actual_methods':report['actual_method_source_audit']['code_entry_counts']}))
except BaseException:
 traceback.print_exc();raise
