"""E2 entry: paired none/front at N=3/5/10/20, five salts each."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime'))
from experiment_common import group_main
if __name__=='__main__':
    group_main('e2')
