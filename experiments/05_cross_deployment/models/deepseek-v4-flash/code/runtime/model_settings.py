import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
PROFILE=json.loads((ROOT/'config/model.json').read_text(encoding='utf-8'))
def template_kwargs(): return dict(PROFILE['template_kwargs'])
def request_settings(): return dict(chat_template_kwargs=template_kwargs(),**PROFILE['request_extra'])
