"""
Static wiring check: every $('id') / getElementById used by the frontend must
exist in index.html, and every data-i18n key must exist in the dictionaries.

Run:  python tools/check_wiring.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

html_ids = set(re.findall(r'id="([^"]+)"', HTML))
js_ids = set(re.findall(r"\$\('([^']+)'\)", JS))
js_ids |= set(re.findall(r"getElementById\('([^']+)'\)", JS))

missing = sorted(i for i in js_ids if i not in html_ids)
unused = sorted(i for i in html_ids if i not in js_ids and not i.startswith("card-"))

# i18n keys
en_block = JS.split("en: {", 1)[1].split("},", 1)[0]
bn_block = JS.split("bn: {", 1)[1].split("},", 1)[0]
keys_en = set(re.findall(r"(\w+):", en_block))
keys_bn = set(re.findall(r"(\w+):", bn_block))
used = set(re.findall(r'data-i18n="([^"]+)"', HTML))
used |= set(re.findall(r"(?<![A-Za-z_$.])t\('([^']+)'\)", JS))
used = {u for u in used if u.strip()}

problems = []
if missing:
    problems.append("JS references ids missing from index.html: " + ", ".join(missing))
if used - keys_en:
    problems.append("data-i18n/t() keys missing from EN dict: " + ", ".join(sorted(used - keys_en)))
if used - keys_bn:
    problems.append("keys missing from BN dict: " + ", ".join(sorted(used - keys_bn)))

print(f"html ids: {len(html_ids)}  js ids: {len(js_ids)}  i18n keys: {len(keys_en)}")
if unused:
    print("note - html ids not touched by js:", ", ".join(unused))
for p in problems:
    print("FAIL:", p)
if problems:
    sys.exit(1)
print("OK - frontend wiring is consistent")
