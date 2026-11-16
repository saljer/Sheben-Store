"""Посты «ищу / куплю» из выгрузки чата Telegram или из текстового файла -> заготовки заказов. Ничего не сохраняет.

  python tools/leads.py "C:\\Users\\...\\ChatExport_2026-11-15\\result.json"     # экспорт Telegram Desktop (JSON или messages.html)
  python tools/leads.py leads.txt                                                # обычный текст: объявления через пустую строку
  python tools/leads.py file --days 30 --all                                     # --all: и те, где бренд не распознан
"""
import argparse
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
p = argparse.ArgumentParser(add_help=False)
p.add_argument("file", nargs="?")
p.add_argument("--days", type=int, default=60)
p.add_argument("--all", action="store_true")
a = p.parse_args()
if not a.file or not Path(a.file).exists():
    print(__doc__)
    raise SystemExit(0 if not a.file else 1)
f = Path(a.file)
sys.argv = sys.argv[:1]
import _feed  # noqa: E402,F401  — журнал во временную папку, путь к модулям Щебня
import goofish_parser_pro as g  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
cat = g.load_catalog(Path(g.build_args(["--listen"]).catalog))
posts = []
if f.suffix.lower() in (".json", ".html"):
    for src, items in g.read_tg_export(f):
        posts += [(src[3:], str(it.get("date", ""))[:10], it.get("url", ""), it["text"]) for it in items]
    if not posts:
        print("В файле нет постов канала или группы (личные переписки не читаются) либо они старше полугода.")
else:
    posts = [("текст", "", "", t.strip()) for t in re.split(r"\n\s*\n", f.read_text(encoding="utf-8", errors="replace")) if t.strip()]
since = (datetime.now() - timedelta(days=a.days)).isoformat()[:10]
out, seen = [], set()
for src, day, url, text in posts:
    if day and day < since:
        continue
    if not g.has_any(g.low(text), g.LEAD_WORDS):
        continue
    key = re.sub(r"\W+", "", g.low(text))[:160]
    if key in seen:
        continue
    seen.add(key)
    lead = g.parse_lead(text, cat)
    if a.all or (lead["brand"] and (lead["budget"] or lead["size"])):
        out.append({"src": src, "date": day, "url": url, "text": re.sub(r"\s+", " ", text)[:300], "order": lead})
print(f"Постов: {len(posts)}, со словами «ищу / куплю»: {len(seen)}, годных в заказ: {len(out)}")
print(json.dumps(out, ensure_ascii=False, indent=1))
