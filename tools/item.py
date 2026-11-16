"""Всё, что Щебень знает об одной вещи, — для текста объявления о продаже. Только чтение (копия базы).

  python tools/item.py 1073216085524          # id вещи из ленты или из «Сделок»
"""
import json
import sys

from _feed import load_feed

if len(sys.argv) < 2:
    print(__doc__)
    raise SystemExit
iid = sys.argv[1].strip()
g, cat, good, bad, db = load_feed()
r = next((x for x in good if x["id"] == iid), None) or next((x for x in bad if x["id"] == iid), None)
deal = next((d for d in g.load_deals(db) if str(d.get("item_id")) == iid), None)
if not r and not deal:
    print("Вещи с таким id нет ни в ленте, ни в сделках (возможно, она в архиве).")
    raise SystemExit(1)
r = r or {}
keys = ("brand", "model", "variant", "category", "size", "condition", "s_cond", "defects", "rarity", "title", "desc", "url", "site",
        "price_cny", "price_jpy", "cost_rub", "sell_rub", "sell_note", "sell_src", "best_channel", "gender", "fake_risk", "checks", "reject")
out = {k: r.get(k) for k in keys if r.get(k) not in (None, "")}
out.update(g.market_note(r, cat, g.market_index(db, cat)) if r else {})
if deal:
    out["deal"] = {k: deal.get(k) for k in ("status", "buy_rub", "cost_rub", "sell_rub", "sold_rub", "channel", "expert", "note", "ts_buy")
                   if deal.get(k) not in (None, "")}
print(json.dumps(out, ensure_ascii=False, indent=1))
