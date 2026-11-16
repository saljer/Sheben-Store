"""Подбор вещей из ленты Щебня под запрос покупателя. Работает на копии базы; с --write кладёт результат
в «Подборку» на «Главной» (picks.json) — больше ничего не меняет.

  python tools/pick.py --brands "Stone Island|Moncler|Prada" --sizes "M,L" --words "羽绒|ダウン|puffer|down" \
      --color black --cost-min 50000 --cost-max 100000 --cond-min 88 --gender м --budget 150000 --write \
      --title "Чёрные пуховики M–L, 50–100 тыс."

Параметры (все необязательные):
  --brands     бренды через |, как в ленте («Stone Island|C.P. Company»)
  --sizes      размеры через запятую: M,L или 48,50 (у Moncler 2 = M, 3 = L — Щебень переводит сам)
  --any-size   брать и вещи, где размер в объявлении не указан
  --words      слова в названии/описании: через пробел — нужны все, варианты одного слова через |
  --color      black | white | grey | blue | green | brown | red | beige
  --type       outerwear | clothing | bag | sneakers | shoes | accessories | jewelry | eyewear
  --cost-min / --cost-max   ваши затраты «под ключ», ₽
  --cond-min   состояние от (0–100; 88 — хорошее, 95 — почти новое)
  --gender     м или ж: вещи другого пола убираются, неясные остаются
  --site       goofish | mercari
  --risk       low (только низкий риск подделки) | any (кроме высокого) — по умолчанию any
  --fresh-days вещь видели не раньше чем N дней назад
  --limit      сколько вещей вывести и записать (по умолчанию 12)
  --budget     бюджет покупателя, ₽: в «Подборке» покажется запас до него
  --title      заголовок «Подборки»
  --ids        готовый список id через запятую (вместо поиска) — чтобы записать выбранное вручную
  --write      записать в picks.json (новая подборка заменяет старую)
"""
import argparse
import json
import re
from datetime import datetime, timedelta

from _feed import ROOT, load_feed

COLORS = {"black": r"黑|ブラック|黒|black|nero", "white": r"白|ホワイト|white|bianco", "grey": r"灰|グレー|grey|gray",
          "blue": r"蓝|藏青|ブルー|ネイビー|blue|navy", "green": r"绿|军绿|グリーン|カーキ|green|khaki|olive",
          "brown": r"棕|咖|ブラウン|brown", "red": r"红|レッド|red|bordeaux", "beige": r"米色|卡其|ベージュ|beige"}

p = argparse.ArgumentParser(add_help=False)
for k in ("brands", "sizes", "words", "color", "type", "gender", "site", "title", "ids"):
    p.add_argument("--" + k, default="")
for k in ("cost-min", "cost-max", "cond-min", "fresh-days", "budget"):
    p.add_argument("--" + k, type=float, default=0)
p.add_argument("--risk", default="any")
p.add_argument("--limit", type=int, default=12)
p.add_argument("--any-size", action="store_true")
p.add_argument("--write", action="store_true")
p.add_argument("-h", "--help", action="store_true")
a = p.parse_args()
if a.help:
    print(__doc__)
    raise SystemExit

g, cat, good, bad, db = load_feed()
brands = {b.strip() for b in a.brands.split("|") if b.strip()}
known = {r["brand"] for r in good}
miss = brands - known
if miss:
    print("В ленте нет бренда:", ", ".join(sorted(miss)), "| есть похожие:",
          ", ".join(sorted(b for b in known if any(m.lower()[:4] in b.lower() for m in miss))[:8]) or "—")
sizes = {g._size_key(s) for s in re.split(r"[,/]", a.sizes) if s.strip()}
color = re.compile(COLORS[a.color], re.I) if a.color in COLORS else None
since = (datetime.now() - timedelta(days=a.fresh_days)).isoformat() if a.fresh_days else ""
ids = [x.strip() for x in a.ids.split(",") if x.strip()]
stat = {}


def drop(why):
    stat[why] = stat.get(why, 0) + 1
    return False


def ok(r):
    t = g.low(f'{r.get("title", "")} {r.get("desc", "")}')
    if brands and r["brand"] not in brands:
        return False
    if a.type and r.get("ckey") != a.type:
        return False
    if a.site and (r.get("site") or "goofish") != a.site:
        return False
    if a.words and not all(any(g.find_pos(t, v) >= 0 for v in w.split("|") if v) for w in g.low(a.words).split()):
        return False
    stat["подходят по бренду, типу и словам"] = stat.get("подходят по бренду, типу и словам", 0) + 1
    if color and not color.search(t):
        return drop("не тот цвет или цвет не назван")
    if a.gender and r.get("gender") and r["gender"] != a.gender:
        return drop("другой пол")
    if sizes:
        if not r.get("size"):
            if not a.any_size:
                return drop("размер не указан")
        elif g._size_key(r["size"]) not in sizes:
            return drop("другой размер")
    c = r.get("cost_rub") or 0
    if (a.cost_min and c < a.cost_min) or (a.cost_max and (not c or c > a.cost_max)):
        return drop("затраты вне диапазона")
    if a.cond_min and (r.get("s_cond") or 0) < a.cond_min:
        return drop("состояние хуже")
    if r.get("fake_risk") == "высокий" or r.get("mark") == "hide" or (a.risk == "low" and r.get("fake_risk") != "низкий"):
        return drop("риск подделки")
    if since and str(r.get("last_seen") or "") < since:
        return drop("давно не видели")
    return True


by = {r["id"]: r for r in good}
rows = [by[i] for i in ids if i in by] if ids else sorted((r for r in good if ok(r)), key=lambda r: -(r.get("total") or 0))
if ids and len(rows) < len(ids):
    print("Нет в ленте (проданы, отсеяны или в архиве):", ", ".join(i for i in ids if i not in by))
print("Отбор:", "; ".join(f"{k} — {v}" for k, v in stat.items()) or "по списку id", f"| итог: {len(rows)}")
rows = rows[:a.limit]
fmt = lambda v: f"{v:,.0f}".replace(",", " ")
for r in rows:
    price = f'¥{fmt(r.get("price_jpy") or 0)} JP' if r.get("site") else f'¥{fmt(r.get("price_cny") or 0)}'
    print(" | ".join(str(x) for x in (
        r.get("total"), r["brand"], r.get("model") or "", f'р.{r.get("size") or "?"}', f'сост.{r.get("s_cond")}',
        {"м": "муж", "ж": "жен"}.get(r.get("gender"), "пол?"), price, f'затраты {fmt(r.get("cost_rub") or 0)} ₽',
        f'риск {r.get("fake_risk")}', f'видели {str(r.get("last_seen") or "")[:10]}', r["id"], r.get("url", ""), r.get("title", "")[:70])))
if a.write and rows:
    notes = {r["id"]: " · ".join(x for x in (f'{r["brand"]} {r.get("model") or ""}'.strip(),
                                             f'размер {r["size"]}' if r.get("size") else "размер не указан") if x) for r in rows}
    out = {"title": (a.title or "Подборка") + f" ({datetime.now():%d.%m})", "budget": int(a.budget), "ids": [r["id"] for r in rows], "notes": notes}
    (ROOT / "picks.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Записано в «Подборку» на «Главной»: {len(rows)} вещей. Появится после ближайшего пересчёта ленты в Щебне.")
elif a.write:
    print("Записывать нечего: ничего не нашлось.")
