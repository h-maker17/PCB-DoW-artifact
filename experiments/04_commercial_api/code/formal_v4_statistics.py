"""Frozen all-assigned matched-block analysis; no execution or credential path."""
from decimal import Decimal as D
import random,json,hashlib
def nullable(reason):return {'estimate':None,'ci95':None,'reason':reason}
def percentile(values,p):return values[int(p*(len(values)-1))]
def ratio_bootstrap(blocks,expected,*,seed=20261012,resamples=10000):
    if [b['repeat'] for b in blocks]!=expected or any(not b['complete'] for b in blocks):return nullable('MISSING_REQUIRED_ASSIGNED_BLOCK_COST')
    numerator=sum((D(b['attack']) for b in blocks),D(0));denominator=sum((D(b['baseline']) for b in blocks),D(0))
    if denominator<=0:return nullable('NONPOSITIVE_BASELINE_SUM')
    rng=random.Random(seed);samples=[];inventory=[]
    for _ in range(resamples):
        indices=[rng.randrange(len(blocks)) for _ in blocks];inventory.append(indices)
        den=sum((D(blocks[i]['baseline']) for i in indices),D(0));num=sum((D(blocks[i]['attack']) for i in indices),D(0))
        if den<=0:return nullable('NONPOSITIVE_BOOTSTRAP_DENOMINATOR')
        samples.append(num/den)
    samples.sort()
    return {'estimate':str(numerator/denominator),'ci95':[str(percentile(samples,.025)),str(percentile(samples,.975))],'reason':None,'numerator_sum':str(numerator),'denominator_sum':str(denominator),'matched_blocks':len(blocks),'small_block_inference_exploratory':len(blocks)<=5,'bootstrap_resamples':resamples,'seed':seed,'unit':'whole matched doc/repeat block, paired arms','percentile_indices':[int(.025*(resamples-1)),int(.975*(resamples-1))],'resampling_index_sha256':hashlib.sha256(json.dumps(inventory,separators=(',',':')).encode()).hexdigest()}
def analyze(plan,measurements,statistics):
    rows=[r for r in plan if r['provider']=='deepseek' and r['phase']=='formal'];assert len(rows)==700 and len({r['request_id'] for r in rows})==700
    per_chain=[];lookup={}
    for n in (5,10,20):
        for repeat in range(1,6):
            for arm in ('baseline','frontzw','frontmeta','end'):
                assigned=[r for r in rows if r['N']==n and r['repeat']==repeat and r['arm']==arm];assert [r['replica'] for r in assigned]==list(range(1,n+1))
                observed=[measurements.get(r['request_id']) for r in assigned];known=all(v is not None and v.get('input_cost') is not None for v in observed);output_known=all(v is not None and v.get('output_cost') is not None for v in observed)
                valid=all(v is not None and v.get('technical_accepted')is True for v in observed)
                ch={'N':n,'repeat':repeat,'arm':arm,'assigned_ids':[r['request_id'] for r in assigned],'required_requests':n,'first_included':True,'complete_cost':known,'complete_output_cost':output_known,'all_technical_accepted':valid,'input_cost':str(sum((D(v['input_cost']) for v in observed),D(0))) if known else None,'output_cost':str(sum((D(v['output_cost']) for v in observed),D(0))) if output_known else None,'uniform_peak_input_cost':str(sum((D(v['uniform_peak_input_cost']) for v in observed),D(0))) if known and all(v.get('uniform_peak_input_cost') is not None for v in observed) else None,'input_tokens':sum(v['input_tokens'] for v in observed) if known else None,'hit_tokens':sum(v['hit_tokens'] for v in observed) if known else None,'miss_tokens':sum(v['miss_tokens'] for v in observed) if known else None,'missing_ids':[r['request_id'] for r,v in zip(assigned,observed) if v is None or v.get('input_cost') is None],'protocol_deviation_ids':[r['request_id'] for r,v in zip(assigned,observed) if v is not None and v.get('technical_accepted')is not True],'baseline_warm_condition':None,'incomplete_reason':None if known else 'ONE_OR_MORE_ASSIGNED_COSTS_UNAVAILABLE'}
                if known:ch['token_weighted_hit']=str(D(ch['hit_tokens'])/D(ch['input_tokens'])) if ch['input_tokens'] else None
                else:ch['token_weighted_hit']=None
                warm=observed[2:]
                if arm=='baseline' and all(v is not None and v.get('input_tokens') is not None and v.get('hit_tokens') is not None for v in warm):
                    inp=sum(v['input_tokens'] for v in warm);hit=sum(v['hit_tokens'] for v in warm);ch['baseline_warm_condition']=D(hit)/D(inp)>=D('0.5') if inp else None
                lookup[(n,repeat,arm)]=ch;per_chain.append(ch)
    endpoints=[]
    for n in (5,10,20):
        for arm in ('frontmeta','frontzw','end'):
            blocks=[];unified=[]
            for repeat in range(1,6):
                a=lookup[(n,repeat,arm)];b=lookup[(n,repeat,'baseline')]
                blocks.append({'repeat':repeat,'complete':a['complete_cost'] and b['complete_cost'],'attack':a['input_cost'],'baseline':b['input_cost']})
                unified.append({'repeat':repeat,'complete':a['uniform_peak_input_cost'] is not None and b['uniform_peak_input_cost'] is not None,'attack':a['uniform_peak_input_cost'],'baseline':b['uniform_peak_input_cost']})
            actual=ratio_bootstrap(blocks,[1,2,3,4,5],seed=statistics['seed'],resamples=statistics['bootstrap_resamples']);fixed=ratio_bootstrap(unified,[1,2,3,4,5],seed=statistics['seed'],resamples=statistics['bootstrap_resamples'])
            selected=[r for r in range(1,6) if lookup[(n,r,'baseline')]['baseline_warm_condition']is True];warmblocks=[b for b in blocks if b['repeat'] in selected]
            conditional=ratio_bootstrap(warmblocks,selected,seed=statistics['seed'],resamples=statistics['bootstrap_resamples']) if len(selected)>=2 else nullable('FEWER_THAN_TWO_WARM_MATCHED_BLOCKS')
            endpoints.append({'N':n,'arm':arm,'role':'PRIMARY' if n==20 and arm=='frontmeta' else 'SECONDARY','chain_N5_exploratory':n==5,'small_five_block_inference_exploratory':True,'actual_usage_priced_input_ratio_of_sums':actual,'uniform_peak_input_diagnostic':fixed,'warm_subset_secondary_only':{'selected_repeats':selected,'selection':'baseline i3..N token weighted hit>=0.5; neverprimaryreplacement','result':conditional},'all_five_blocks_required_for_unconditional':True})
    primary=next(e for e in endpoints if e['role']=='PRIMARY')
    return {'primary':primary,'endpoints':endpoints,'chains':per_chain,'primary_estimable':primary['actual_usage_priced_input_ratio_of_sums']['estimate'] is not None,'all700_costs_available':all(c['complete_cost'] for c in per_chain),'all700_technical_accepted':all(c['all_technical_accepted'] for c in per_chain),'OpenAI_included':False,'I_group_complete':False,'missing_costs_never_zero':True,'cost_dependent_truncation_limitation':'Budget STOP is potentially outcome/cost dependent; missingness is not random. All assigned primary blocks remain required.','no_internal_KV_mechanism_inference':True}
