"""Frozen rank8 shared integration; preparation never grants formal admission."""
def run(ctx,row):
    from native_bridge import ROOT,launch
    from selection_gate import verify_rank8_preparation,verify_continuation,read
    verify_rank8_preparation(row)
    if ctx.journal_path.resolve().is_relative_to((ROOT/'exec/pilot').resolve()):
        from pipeline import code_revision
        verify_continuation(read('config/frameworks.json'),row['repo'],code_revision()['commit'])
    launch(ctx,row,'preparation/crewai_night/venv','ca','crewai_support/native_entry.py')
