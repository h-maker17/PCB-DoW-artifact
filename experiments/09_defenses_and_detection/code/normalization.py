"""H1 pipeline v1; apply only to declared tool-return bytes decoded as UTF-8.

The aggressive whitespace policy is deliberately audited on code/YAML/ZWJ.
It is not claimed to preserve semantics in arbitrary languages or formats.
"""
import unicodedata, re

VERSION = 'H1-v1-strip-Cf-Cc-except-tab-LF-CR-NFC-collapse-whitespace'


def normalize(text):
    stripped = ''.join(c for c in text if unicodedata.category(c) not in ['Cf', 'Cc'] or c in '\t\n\r')
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFC', stripped)).strip()


def selftest():
    cases = [('zwsp', '\u200b' * 5 + 'Grounding data', 'Grounding data'),
             ('nbsp', 'Grounding\u00a0\u00a0data', 'Grounding data'), ('zwj', 'Grounding\u200ddata', 'Groundingdata'),
             ('whitespace', 'Grounding\t  data', 'Grounding data'),
             ('request-id', '[request-id:5]\nGrounding data', '[request-id:5] Grounding data'),
             ('timestamp', '[2026-10-07T12:00:05Z]\nGrounding data', '[2026-10-07T12:00:05Z] Grounding data'),
             ('html-comment', '<!--nonce:5-->\nGrounding data', '<!--nonce:5--> Grounding data')]
    rows = [{'marker': name, 'input': text, 'normalized': normalize(text), 'expected': expected,
             'pass': normalize(text) == expected} for name, text, expected in cases]
    assert all(r['pass'] for r in rows)
    return rows
