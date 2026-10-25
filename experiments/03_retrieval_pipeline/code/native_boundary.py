import uuid, httpx
from autogen_core.tools import FunctionTool
from autogen_agentchat.agents import AssistantAgent
from autogen_ext.models.openai import OpenAIChatCompletionClient
from common import *
from retrieval import RetrievalService
from pipeline import POLICY, vote


class NativeToolClient(OpenAIChatCompletionClient):
    async def create(self, *args, **kwargs):
        # The model still generates the native tool call; only the allowed tool is selected by policy.
        # Reflection has no tools and remains an ordinary answer request.
        if kwargs.get('tools'):
            kwargs['tool_choice'] = self.selected_tool
        return await super().create(*args, **kwargs)


async def run_native(mode, doc, N, phase='formal'):
    dest = R / 'data' / phase / f'{doc["task"]}-{doc["id"]}-n{N}-{mode}'
    if (dest / 'result.json').exists(): return read(dest / 'result.json')
    if dest.exists(): raise RuntimeError('Partial native round, inspect rather than overwrite')
    dest.mkdir(parents=True);
    salt = uuid.uuid4().hex;
    service = RetrievalService('benign' if mode == 'functiontool-benign' else 'pretrieval-zerowidth', doc['question']);
    history = read(R / 'datasets/shared-history.json')['text'];
    rows = [];
    answers = [];
    cached_sequences = []
    for replica in range(1, N + 1):
        raw = []

        async def capture(response):
            await response.aread()
            if response.request.url.path == '/v1/chat/completions':
                item = {'request': json.loads(response.request.content), 'response': response.json(),
                        'received_ns': now_ns()};
                raw.append(item);
                save(dest / f'{replica:04d}-{len(raw)}-raw.json', item)

        transport = httpx.AsyncClient(event_hooks={'response': [capture]}, timeout=600)
        client = NativeToolClient(model=MODEL, base_url='http://127.0.0.1:8000/v1', api_key='local-only',
                                  http_client=transport,
                                  model_info={'vision': False, 'function_calling': True, 'json_output': False,
                                              'family': 'unknown', 'structured_output': False}, temperature=0, seed=42,
                                  top_p=1, max_tokens=2048, extra_body={'cache_salt': salt, 'return_token_ids': True},
                                  include_name_in_message=False, add_name_prefixes=False, max_retries=0)
        tool = FunctionTool(service.retrieve, name='search_documents',
                            description='Retrieve grounding for the fixed current user question. Takes no arguments. Call once before answering.')
        client.selected_tool = tool
        agent = AssistantAgent(name=f'replica_{replica}', model_client=client,
                               system_message=history + POLICY + '\nFirst call search_documents exactly once; then answer using its retrieved passages.',
                               tools=[tool], reflect_on_tool_use=True, max_tool_iterations=1)
        result = await agent.run(task=doc['question'] + f'\nReplica number: {replica}');
        await client.close();
        save(dest / f'{replica:04d}-agent-events.json', [m.model_dump(mode='json') for m in result.messages]);
        save(dest / 'tool-calls.json', service.calls)
        assert len(raw) == 2 and len(service.calls) == replica, 'Native tool+reflection must be observed, not assumed'
        call = service.calls[-1];
        assert raw[0]['received_ns'] <= call['started_ns'] <= call['finished_ns'] <= raw[1]['received_ns']
        # vLLM named-choice returns stop with a structured tool_call; validate the call and linked execution below.
        assert raw[0]['response']['choices'][0]['finish_reason'] in ['stop', 'tool_calls'] and \
               raw[1]['response']['choices'][0]['finish_reason'] in ['stop', 'length']
        second = raw[1]['request']['messages'];
        tools = [m for m in second if m['role'] == 'tool'];
        assert len(tools) == 1 and tools[0]['content'] == call['returned_text'];
        assert not any(m['role'] == 'tool' for m in raw[0]['request']['messages'])
        generated = raw[0]['response']['choices'][0]['message']['tool_calls'];
        assert len(generated) == 1 and generated[0]['id'] == tools[0]['tool_call_id'] and generated[0]['function'][
            'name'] == 'search_documents' and json.loads(generated[0]['function']['arguments']) == {}
        stage = []
        for turn, item in enumerate(raw, 1):
            q, s = item['request'], item['response'];
            ids = s['prompt_token_ids'];
            u = s['usage'];
            assert ids == api('/tokenize', {'model': MODEL, 'messages': q['messages'],
                                            **({'tools': q['tools']} if q.get('tools') else {})})['tokens'];
            c = u['prompt_tokens_details']['cached_tokens'];
            assert type(c) is int and 0 <= c <= u['prompt_tokens'] == len(ids)
            assert q['cache_salt'] == salt and q['temperature'] == 0 and q['seed'] == 42
            if replica == 1 and turn == 1: assert c == 0
            decoded = native_decode(ids);
            (dest / f'{replica:04d}-{turn}-prompt.txt').write_text(decoded, encoding='utf-8')
            if turn == 2:
                # Llama's native tool template JSON-serializes tool content (newlines become literal escapes).
                # Verbatim role:tool content and native tokenization equality were verified above.
                tool_header = '<|start_header_id|>ipython<|end_header_id|>'
                assert history in decoded and tool_header in decoded and decoded.index(history) < decoded.index(
                    tool_header)
                item[
                    'native_tool_serialization'] = 'Llama ipython role, JSON string content; raw role:tool content is verbatim'
            best = max((lcp(p, ids) for p in cached_sequences), default=0);
            prediction = 16 * (min(best, len(ids) - 1) // 16)
            stage.append({'turn': turn, 'prompt': len(ids), 'cached': c, 'completion': u['completion_tokens'],
                          'native_token_sha256': digest(ids), 'max_lcp_prior_prompt_and_completion': best,
                          'block_prediction': prediction, 'prediction_difference': c - prediction})
            completion = s['choices'][0].get('token_ids') or [];
            cached_sequences.append(ids + completion[:-1])
        answer = result.messages[-1].content;
        assert isinstance(answer, str) and answer == raw[1]['response']['choices'][0]['message']['content'];
        answers.append(answer)
        rows.append({'replica': replica, 'answer': answer,
                     'answer_complete': raw[1]['response']['choices'][0]['finish_reason'] == 'stop',
                     'reflection_finish_reason': raw[1]['response']['choices'][0]['finish_reason'], 'stages': stage,
                     'prompt': sum(x['prompt'] for x in stage), 'cached': sum(x['cached'] for x in stage),
                     'tool_return_sha256': call['returned_sha256'], 'reflection_prompt': stage[1]['prompt'],
                     'reflection_cached': stage[1]['cached'], 'A2a_independent': True, 'A2b_pre_generation': False,
                     'A3_grounding_first': False, 'native_midgeneration_append': True})
    p = sum(x['prompt'] for x in rows);
    c = sum(x['cached'] for x in rows);
    out = {'phase': phase, 'mode': mode, 'task': doc['task'], 'id': doc['id'], 'query': doc['question'], 'N': N,
           'cache_salt': salt, 'workers': rows, 'model_requests': 2 * N, 'tool_calls': N,
           'unique_tool_return_bytes': len({x['returned_sha256'] for x in service.calls}),
           'top_k_ids': service.calls[0]['top_k_ids'], 'prompt_tokens': p, 'cached_tokens': c,
           'warm_hit': sum(x['cached'] for x in rows[1:]) / sum(x['prompt'] for x in rows[1:]),
           'reflection_warm_hit': sum(x['reflection_cached'] for x in rows[1:]) / sum(
               x['reflection_prompt'] for x in rows[1:]), 'costs': {str(f): p - c + f * c for f in [.1, .25, .5]},
           'aggregate_answer': vote(answers, doc['task']),
           'cost_scope': 'tool-generation plus reflection; matched functiontool-benign only',
           'A2_independent_pre_generation': False, 'A3_grounding_first': False}
    assert len({digest(c['top_k_ids']) for c in service.calls}) == 1;
    save(dest / 'result.json', out);
    return out
