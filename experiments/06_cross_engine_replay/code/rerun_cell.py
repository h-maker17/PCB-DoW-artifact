"""One-command independently isolated rerun without overwriting formal evidence."""
from pathlib import Path
import sys, argparse, uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
from engine import R, ensure
from replay import read, run_cell

ap = argparse.ArgumentParser();
ap.add_argument('--key', required=True);
ap.add_argument('--page', type=int, default=1);
ap.add_argument('--tag', default=None);
args = ap.parse_args()
run = next(r for r in read(R / 'datasets/replay-plan.json')['runs'] if r['key'] == args.key)
ensure(run['model'], args.page)
run_cell(run, args.page, args.tag or 'rerun-' + uuid.uuid4().hex)
