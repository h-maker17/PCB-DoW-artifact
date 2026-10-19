"""Frozen rank7 conditional shared entry; formal execution is gated independently."""
from pathlib import Path
def run(ctx,row):
    from native_bridge import ROOT,launch
    from selection_gate import verify_rank7_preparation,verify_continuation,read
    verify_rank7_preparation(row)
    # A reports contract is authorized preparation. Any formal output location
    # additionally requires top6 acceptance, extension trigger and exact commit.
    if ctx.journal_path.resolve().is_relative_to((ROOT/'exec/pilot').resolve()):
        from pipeline import code_revision
        verify_continuation(read('config/frameworks.json'),row['repo'],code_revision()['commit'])
    launch(ctx,row,'preparation/autogen_night/env','ag','autogen_support/native_entry.py')
