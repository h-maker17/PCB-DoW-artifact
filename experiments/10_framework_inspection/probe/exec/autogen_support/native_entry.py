"""Launch byte-frozen AutoGen native candidate with attempt-private temp/cache."""
from pathlib import Path
import argparse,json,os,sys
ROOT=Path(__file__).resolve().parents[2]
def main(config):
    state=Path(config['state_dir']).resolve()
    if not state.is_relative_to(ROOT):raise RuntimeError('Native state outside SCAN project')
    private=state/'process_private';private.mkdir(parents=True,exist_ok=False)
    paths={key:private/name for key,name in [('TEMP','temp'),('TMP','temp'),('HF_HOME','huggingface'),('XDG_CACHE_HOME','cache')]}
    for path in paths.values():path.mkdir(parents=True,exist_ok=True)
    for key,path in paths.items():os.environ[key]=str(path)
    os.environ['PYTHONDONTWRITEBYTECODE']='1'
    sys.path.insert(0,str(ROOT/'preparation/autogen_night'))
    import adapter_autogen_integration as native
    # Frozen base import establishes original source/model paths. Restore only
    # temp/cache destinations; keep its native ONNX factory/providers/model.
    for key,path in paths.items():os.environ[key]=str(path)
    return native.native(config)
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--native-input',required=True)
    main(json.loads(Path(parser.parse_args().native_input).read_bytes()))
