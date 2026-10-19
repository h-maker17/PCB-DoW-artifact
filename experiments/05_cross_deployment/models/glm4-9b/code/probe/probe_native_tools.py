import json,uuid,urllib.request
from pathlib import Path
r=Path('/path/to/workspace/多智能体系统实验数据/fcb-dow-research/experiments/2026-10-03-c-remaining3-models/models/glm4-9b')
s=Path((r/'config/latest-session.txt').read_text().strip())
assert json.loads((s/'status.json').read_text())['phase']=='complete','Do not interleave with formal runs'
req=dict(model='glm-4-9b-0414',messages=[dict(role='user',content='Use the calculate tool to compute 7 * 8.')],
    tools=[dict(type='function',function=dict(name='calculate',description='Compute an arithmetic expression.',parameters=dict(type='object',properties=dict(expression=dict(type='string')),required=['expression'])))],
    tool_choice='auto',temperature=0,seed=42,max_tokens=128,stream=False,return_token_ids=True,cache_salt=uuid.uuid4().hex)
(r/'evidence/native-tool-probe.request.json').write_text(json.dumps(req,indent=2))
request=urllib.request.Request('http://127.0.0.1:8000/v1/chat/completions',data=json.dumps(req).encode(),headers={'Content-Type':'application/json'})
with urllib.request.urlopen(request,timeout=120) as response:res=json.load(response)
(r/'evidence/native-tool-probe.response.json').write_text(json.dumps(res,ensure_ascii=False,indent=2),encoding='utf-8')
message=res['choices'][0]['message'];calls=message.get('tool_calls') or []
valid=bool(calls) and calls[0]['function']['name']=='calculate' and bool(json.loads(calls[0]['function']['arguments']).get('expression'))
(r/'evidence/native-tool-probe-check.json').write_text(json.dumps(dict(native_tool_response_parsed=valid,not_formal_C_data=True,raw_response=str(r/'evidence/native-tool-probe.response.json')),indent=2))
print('NATIVE_TOOL_PROBE',valid,json.dumps(message,ensure_ascii=False),flush=True)
