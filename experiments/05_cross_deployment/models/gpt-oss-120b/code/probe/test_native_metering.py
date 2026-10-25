import asyncio, json, os, sys
from pathlib import Path
from types import SimpleNamespace

r = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(r / 'code/service'))
os.environ['LAB_SERVER_EVIDENCE'] = str(r / 'evidence/metering-replay-check')
Path(os.environ['LAB_SERVER_EVIDENCE']).mkdir(exist_ok=True)
os.environ['TIKTOKEN_RS_CACHE_DIR'] = str(r / 'cache/harmony')
os.environ.update(HTTPS_PROXY='http://127.0.0.1:9', HTTP_PROXY='http://127.0.0.1:9', ALL_PROXY='http://127.0.0.1:9',
                  NO_PROXY='')
from harmony_tokenize_patch import install_harmony_tokenize_patch

install_harmony_tokenize_patch()
from vllm.entrypoints.openai.protocol import TokenizeChatRequest
from vllm.entrypoints.openai.serving_tokenization import OpenAIServingTokenization

session = Path((r / 'config/latest-session.txt').read_text().strip());
d = next((session / 'probe').glob('*-mapshared-prefix-*'))


async def check_model(request): return None


async def run():
    results = []
    for i in (1, 2):
        stem = f'{i:04d}-worker_{i}'
        req = json.loads((d / (stem + '.request.json')).read_text());
        res = json.loads((d / (stem + '.response.json')).read_text())
        tok = TokenizeChatRequest(**{k: req[k] for k in ('model', 'messages', 'tools', 'chat_template_kwargs')})
        obj = SimpleNamespace(model_config=SimpleNamespace(hf_config=SimpleNamespace(model_type='gpt_oss')),
                              _check_model=check_model, max_model_len=32768)
        actual = await OpenAIServingTokenization.create_tokenize(obj, tok, None)
        assert actual.tokens == res['prompt_token_ids'], (i, len(actual.tokens), len(res['prompt_token_ids']))
        results.append(dict(worker=i, recorded_real_request=str(d / (stem + '.request.json')),
                            actual_completion_ids_equal_replayed_native_metering=True, tokens=actual.count))
    (r / 'evidence/native-harmony-metering-replay-check.json').write_text(json.dumps(results, indent=2))
    print('HARMONY_METERING_MATCHES_RECORDED_REAL_INPUTS', results, flush=True)


asyncio.run(run())
