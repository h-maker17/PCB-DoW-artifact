from pathlib import Path
import argparse,asyncio,json,sys,traceback
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'exec'))
from native_crew import execute_candidate
def main(config):
    outer=Path(config['state_dir']).resolve()
    if not (outer.is_relative_to(ROOT/'reports') or outer.is_relative_to(ROOT/'exec/pilot')):raise RuntimeError('Native state outside owned run directories')
    config={**config,'state_dir':str(outer/'worker')}
    try:
        report=asyncio.run(execute_candidate(config))
        receipt={**report,'private_state':config['state_dir'],'source_overlay_wheel_parity_claimed':False,'classification_performed':False}
        (outer/'native_complete.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2,default=str),encoding='utf8')
    except BaseException as error:
        (outer/'native_failure.json').write_text(json.dumps({'native_recipe_completed':False,'error':str(error),'traceback':traceback.format_exc(),'private_state':config['state_dir']},ensure_ascii=False,indent=2),encoding='utf8');raise
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--native-input',required=True);args=parser.parse_args();main(json.loads(Path(args.native_input).read_bytes()))
