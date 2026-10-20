"""User-only candidate entry. Default dry-run; no key files or secret environment reads."""
from pathlib import Path
import argparse,getpass,json,sys,warnings
import protocol_core as core
from http_adapters import Credential,AdapterFailure
from runtime_gate import RuntimeGate,validate_static
ROOT=core.ROOT
DEFAULT_CONFIG=ROOT/'config/protocol_v1.3_fixed_length_proposal.json'
DEFAULT_PLAN=ROOT/'config/compiled_plan_v1.3_proposal.json'
def allowed_file(value,base,pattern):
    path=Path(value).resolve(); parent=(ROOT/base).resolve()
    if not path.is_relative_to(parent) or not path.match(pattern): raise core.GateError('UNAPPROVED_INPUT_PATH')
    if any(x in path.name.lower() for x in ('api_key','apikey','secret','credential')): raise core.GateError('SENSITIVE_FILE_FORBIDDEN')
    return path
def precredential_gate(config,provider,phase,plan,config_path,runtime=None):
    validate_static(config,plan,getattr(runtime,'recovery_review',None),getattr(runtime,'reconciliation_review',None))
    if type(runtime)is not RuntimeGate or runtime.ledger.binding['config']!=core.sha(config) or runtime.ledger.binding['plan']!=core.sha(plan): raise core.GateError('BOUND_RUNTIME_GATE_REQUIRED')
    if str(Path(config_path).resolve())!=runtime.input_paths[0]: raise core.GateError('FROZEN_CONFIG_PATH_MISMATCH')
    return runtime.before_credential(provider,phase)
def manual_credential(provider):
    if not sys.stdin.isatty() or not sys.stderr.isatty(): raise core.GateError('NO_SECURE_USER_TTY')
    with warnings.catch_warnings():
        warnings.simplefilter('error',getpass.GetPassWarning)
        try: value=getpass.getpass('Enter '+provider+' research account key locally (not saved): ')
        except BaseException: raise core.GateError('SECURE_PROMPT_ABORTED') from None
    if not value: raise core.GateError('NO_CREDENTIAL')
    return Credential(value,provider)
def technical_completion_summary(gate):
    gate.require_new_request_permission()
    external=getattr(gate,'reconciliation_review',None) is not None
    result={'technical_calibration_completed':True,'logical_observations':11,'retained_legacy_UNKNOWN_CNY':str(gate.ledger.totals('deepseek')[1]),'formal_enabled':False,'replacement_only':True}
    if external:
        result.update(historical_external_bill_covered_observations=1,new_clock_validated_calibration_observations=10,historical_clock_criterion='FAILED',all_original_clock_criteria_passed=False)
    return result
def run_bound_workflow(gate,config,plan,config_path,provider=None,phase=None,prompt=manual_credential):
    """Run the bound entry loop; fixture tests never call the live launcher."""
    gate.require_new_request_permission()
    credentials={}
    try:
        # Check the journal before attempting the next (out-of-scope) stage.
        # A completed recovery is a terminal success on reopen as well.
        state=gate.state()
        if gate.recovery is not None and state['cursor']==11 and not state['stopped_reason']:
            print(json.dumps(technical_completion_summary(gate))); return 0
        precredential_gate(config,provider,phase,plan,config_path,gate)
        while gate.state()['next_id'] is not None:
            state=gate.state()
            if gate.recovery is not None and state['cursor']==11 and not state['stopped_reason']:
                print(json.dumps(technical_completion_summary(gate))); return 0
            row=precredential_gate(config,None,None,plan,config_path,gate); current_provider=row['provider']
            if current_provider not in credentials:
                credential=prompt(current_provider)
                if type(credential)is not Credential or credential.provider!=current_provider:
                    if isinstance(credential,Credential): credential.clear()
                    raise core.GateError('PROVIDER_CREDENTIAL_MISMATCH')
                credentials[current_provider]=credential; gate.adapters[current_provider].credential=credential
            # Repeat after prompt: persisted inputs may change while TTY waits.
            gate.before_credential(current_provider,row['phase']); gate.step(row['request_id']); gate.accept_completed()
        print(json.dumps({'workflow_completed':True,'requests':len(gate.schedule)})); return 0
    finally:
        for credential in credentials.values(): credential.clear()
def run_history_only(gate):
    state=gate.state()
    if not state.get('history_only') or state['phase']!='HISTORY_ONLY_COMPLETE' or state['cursor']!=1 or state['next_id'] is not None or state['stopped_reason'] or state.get('new_request_allowance')!=0:raise core.GateError('HISTORY_ONLY_COMPLETION_REQUIRED')
    print(json.dumps({'status':'HISTORY_ONLY_RECONCILIATION_COMPLETED','historical_technical_coverage':1,'new_request_allowance':0,'new_API_requests':0,'retained_legacy_UNKNOWN_CNY':str(gate.ledger.totals('deepseek')[1]),'externally_reconciled_charge_CNY':str(gate.ledger.totals('deepseek')[0]),'historical_clock_criterion':'FAILED','cache_initial_state':'UNKNOWN','excluded_from_formal_effect_statistics':True,'technical_calibration_completed':False,'formal_enabled':False}))
    return 0

def main(argv=None,prompt=manual_credential):
    parser=argparse.ArgumentParser(description='Offline by default; candidate requires independent paid runtime acceptance.')
    parser.add_argument('--config',default=str(DEFAULT_CONFIG)); parser.add_argument('--plan',default=str(DEFAULT_PLAN))
    # Legacy selectors restrict the CURRENT global cursor only. They never filter/reorder a plan.
    parser.add_argument('--provider',choices=('deepseek','openai')); parser.add_argument('--phase',choices=('calibration','shape','pilot','formal'))
    parser.add_argument('--recovery-review',default=None)
    parser.add_argument('--reconciliation-review',default=None)
    parser.add_argument('--history-only',action='store_true')
    parser.add_argument('--live',action='store_true'); args=parser.parse_args(argv)
    gate=None
    try:
        if args.reconciliation_review is not None and not args.history_only:raise core.GateError('HISTORY_ONLY_EXPLICIT_ENTRY_REQUIRED')
        if args.history_only and (args.live or args.provider is not None or args.phase is not None or args.recovery_review is not None or args.reconciliation_review is None):raise core.GateError('HISTORY_ONLY_ENTRY_CONFLICT')
        config_path=allowed_file(args.config,'config','protocol_*.json'); config=json.loads(config_path.read_text(encoding='utf-8-sig'))
        if args.history_only:
            plan_path=allowed_file(args.plan,'config','compiled_plan*.json');plan=json.loads(plan_path.read_text(encoding='utf-8'))
            validate_static(config,plan,None,args.reconciliation_review)
            gate=RuntimeGate.open(config,plan,config_path,plan_path,reconciliation_review=args.reconciliation_review)
            return run_history_only(gate)
        if not args.live:
            print(json.dumps({'mode':'DRY_RUN','network':False,'credential_loaded':False,'paid_enabled':config['paid_enabled'],'frozen':config['frozen'],'execution_scope':config.get('bootstrap_scope',{}).get('kind','COMMERCIAL_ALL_STAGE'),'count_enabled':False,'do_not_launch_live_yet':True})); return 0
        if not all(config.get(k)is True for k in ('paid_enabled','frozen','independent_review_approved','credentials_configured','network_enabled','credential_loading_enabled')): raise core.GateError('CLOSED_GATES')
        if config.get('bootstrap_scope') and config.get('runtime_mode')=='OFFLINE_FIXTURE': raise core.GateError('FIXTURE_CANNOT_USER_LAUNCH')
        plan_path=allowed_file(args.plan,'config','compiled_plan*.json'); plan=json.loads(plan_path.read_text(encoding='utf-8'))
        validate_static(config,plan,args.recovery_review,args.reconciliation_review)
        gate=RuntimeGate.open(config,plan,config_path,plan_path,args.recovery_review,args.reconciliation_review); gate.accept_completed()
        return run_bound_workflow(gate,config,plan,config_path,args.provider,args.phase,prompt)
    except (core.GateError,AdapterFailure):
        if gate is not None and not args.history_only: gate.stop_recovery()
        print(json.dumps({'status':'BLOCKED_OR_STOPPED','network_ready_not_assumed':True}),file=sys.stderr); return 2
    except BaseException:
        if gate is not None and not args.history_only:
            for provider in gate.ledger.config['providers']:
                try: gate.ledger.pause(provider,'ENTRY_OR_STORAGE_FAILED')
                except Exception: pass
        print(json.dumps({'status':'STOPPED_INTERNAL_ERROR','details_redacted':True}),file=sys.stderr); return 3
    finally:
        if gate is not None: gate.close()
if __name__=='__main__': raise SystemExit(main())
