"""Decode GLM-4 dense native assistant metadata + JSON; no prompt rewrites."""
import json
import re
from vllm.entrypoints.openai.tool_parsers.abstract_tool_parser import ToolParser,ToolParserManager
from vllm.entrypoints.openai.protocol import ExtractedToolCallInformation,ToolCall,FunctionCall

@ToolParserManager.register_module('glm4_native')
class Glm4NativeToolParser(ToolParser):
    def extract_tool_calls(self,model_output,request):
        names={t.function.name for t in request.tools or [] if hasattr(t,'function')}
        text=model_output.strip()
        if text.startswith('<|assistant|>'):text=text[len('<|assistant|>'):].lstrip()
        found=re.match(r'([A-Za-z_][A-Za-z0-9_]*)\s*\n\s*(\{.*\})\s*(?:<\|(?:observation|user|assistant)\|>)?\s*$',text,re.S)
        if found and found.group(1) in names:
            try:
                args=json.loads(found.group(2))
                if isinstance(args,dict):
                    return ExtractedToolCallInformation(tools_called=True,
                        tool_calls=[ToolCall(type='function',function=FunctionCall(name=found.group(1),arguments=json.dumps(args,ensure_ascii=False)))],content=None)
            except json.JSONDecodeError:pass
        return ExtractedToolCallInformation(tools_called=False,tool_calls=[],content=model_output)

    def extract_tool_calls_streaming(self,*args,**kwargs):
        raise NotImplementedError('C protocol uses only non-stream requests')
