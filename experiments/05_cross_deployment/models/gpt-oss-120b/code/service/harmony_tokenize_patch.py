"""Meter /tokenize with vLLM's unchanged production Harmony assembler."""
import hashlib,inspect,json,os
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace

def install_harmony_tokenize_patch():
    if version('vllm')!='0.11.2':raise RuntimeError('Harmony metering adapter requires audited vLLM0.11.2')
    from vllm.entrypoints.openai.serving_chat import OpenAIServingChat
    from vllm.entrypoints.openai.serving_tokenization import OpenAIServingTokenization
    from vllm.entrypoints.openai.protocol import ChatCompletionRequest,TokenizeChatRequest,TokenizeResponse
    native=OpenAIServingChat._make_request_with_harmony
    native_source=inspect.getsource(native)
    # The same installed function is called, without copying or rewriting it.
    if 'render_for_completion(messages)' not in native_source or 'get_developer_message(tools=request.tools)' not in native_source:
        raise RuntimeError('Native Harmony assembler differs from audited source')
    original=OpenAIServingTokenization.create_tokenize
    original_source=inspect.getsource(original)
    evidence=Path(os.environ['LAB_SERVER_EVIDENCE'])
    (evidence/'native-harmony-assembler.py').write_text(native_source,encoding='utf-8')
    (evidence/'original-tokenize-method.py').write_text(original_source,encoding='utf-8')

    async def tokenize_using_native_harmony(self,request,raw_request):
        if not isinstance(request,TokenizeChatRequest) or self.model_config.hf_config.model_type!='gpt_oss':
            return await original(self,request,raw_request)
        error=await self._check_model(request)
        if error is not None:return error
        if request.chat_template or request.continue_final_message or not request.add_generation_prompt or request.add_special_tokens:
            return self.create_error_response('Harmony metering supports only unmodified native chat assembly with default generation prompt')
        kwargs=request.chat_template_kwargs or {}
        unexpected=set(kwargs)-{'reasoning_effort'}
        if unexpected:return self.create_error_response('Unsupported Harmony metering template kwargs: '+str(sorted(unexpected)))
        chat=ChatCompletionRequest(model=request.model,messages=request.messages,tools=request.tools,
            reasoning_effort=kwargs.get('reasoning_effort'),stream=False)
        _,_,prompts=native(SimpleNamespace(supports_browsing=False,supports_code_interpreter=False),chat)
        ids=prompts[0]['prompt_token_ids']
        strings=None
        if request.return_token_strs:
            tokenizer=await self.engine_client.get_tokenizer();strings=tokenizer.convert_ids_to_tokens(ids)
        return TokenizeResponse(tokens=ids,token_strs=strings,count=len(ids),max_model_len=self.max_model_len)

    OpenAIServingTokenization.create_tokenize=tokenize_using_native_harmony
    metadata=dict(vllm=version('vllm'),purpose='Align /tokenize metering with the unchanged production Harmony assembler',
        original_tokenize_sha256=hashlib.sha256(original_source.encode()).hexdigest(),
        native_harmony_assembler_sha256=hashlib.sha256(native_source.encode()).hexdigest(),
        adapter_source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        chat_completions_input_assembler_modified=False,model_prompts_modified=False,
        installed_files_modified=False,scope='this one GPT server process only')
    (evidence/'harmony-tokenize-adapter.json').write_text(json.dumps(metadata,indent=2),encoding='utf-8')
    print('NATIVE_HARMONY_TOKENIZE_ADAPTER',json.dumps(metadata),flush=True)
