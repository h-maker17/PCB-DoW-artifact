"""Proposed shared adapter entry. Copy/register only after parent extension gate.

This file is preparation-only and is not imported by the current shared pipeline.
"""
def run(ctx,row):
    from native_bridge import ROOT,launch
    launch(ctx,row,'preparation/autogen_night/env','ag',
           str(ROOT/'preparation/autogen_night/adapter_autogen_integration.py'))
