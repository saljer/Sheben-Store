"""Страницы: лента, обучение, быстрая разметка, каталог/цены, главная, сделки, статус."""
from __future__ import annotations

from gp_market import *  # noqa: F401,F403


# ---------------------------------------------------------------- отчёт
ALL_FIELDS = ["status", "first_seen", "last_seen", "prev_price", "mark", "id", "title", "price_cny", "city",
              "seller", "want", "free_ship", "tags", "posted", "drop", "url", "image", "queries"]
REPORT_FIELDS = ["id", "total", "tier", "grail", "s_profit", "s_liq", "s_cond", "s_desire", "s_trust", "category",
                 "brand", "model", "variant", "subtype", "title", "price_cny", "prev_price", "price_vs_median",
                 "deal_pct", "deal_n", "deal_med", "sim_photo", "sim_text",
                 "max_buy_cny", "sell_rub", "best_channel", "profit_rub", "rarity", "low_signals", "red_flags",
                 "defects", "condition", "city", "want", "status", "first_seen", "mark", "url", "image",
                 "fake_risk", "risk_why", "checks", "tags", "outside", "outside_brand", "verdict_why", "has_desc",
                 "size", "size_bonus", "src", "ai_txt", "ai_risk", "labels", "cost_rub", "seller", "sell_note", "sell_src", "gender", "ckey", "kg", "kg_src", "deal", "relist_price", "ru_mkt", "ru_src", "ru_by", "ru_diff", "sq",
                 "posted", "dem_day", "dem_med", "dem_n", "dem_adj", "site", "price_jpy", "max_buy_jpy", "jp_cond", "jp_sold"]




def render_html(good: list[dict], bad: list[dict], reasons: dict, rate: float,
                live: bool = False, version: int = 0, refresh: int = 0, extra: dict | None = None) -> str:
    """Отчёт: живой (сервер --listen, с кнопками) или файл out/best.html."""
    # страница лёгкая: пустые поля не пишем, длинные «Что проверить» одинаковы у вещей одного бренда — пишем один раз
    checks: dict[str, int] = {}
    # В приложении (live) вещей в странице нет: их по 50 штук отдаёт /feed (feed_query) — страница весит ~100 КБ,
    # а не 55 МБ, и открывается сразу. Файл-снимок best.html держит FEED_MAX лучших (good уже отсортирован)
    total_good = len(good)
    facets = {"cats": sorted({r["category"] for r in good}), "brands": sorted({r["brand"] for r in good})} if live else None
    good = [] if live else good[:FEED_MAX]
    payload = {
        "live": live, "version": version, "rate": rate, "fields": REPORT_FIELDS,
        "good_total": total_good, "paged": live, "facets": facets,
        "good": [slim_row(r, checks) for r in good],
        "bad": [{k: r.get(k, "") for k in ("reject", "title", "price_cny", "url")} for r in bad[:400]],
        "bad_total": len(bad),
        "reasons": sorted(reasons.items(), key=lambda x: -x[1]),
    }
    payload["checks"] = list(checks)
    payload.update(extra or {})
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    meta = f'<meta http-equiv="refresh" content="{refresh}">' if refresh and not live else ""
    return REPORT_HTML.replace("__META__", meta).replace("__DATA__", data)


FEED_MAX = 8000     # вещей в файле-снимке best.html
FEED_PAGE = 50      # вещей за один запрос ленты


def slim_row(r: dict, checks: dict | None = None) -> dict:
    """Вещь для страницы: пустые поля не пишем; длинные «Что проверить» в снимке — номером из общего словаря."""
    o = {}
    for k in REPORT_FIELDS:
        v = r.get(k, "")
        if v is None or v is False or v == "":      # 0 оставляем: нулевая прибыль — тоже значение
            continue
        o[k] = checks.setdefault(v, len(checks)) if k == "checks" and checks is not None else v
    return o


def _fnum(v):
    """Число из поля строки ("" и None — нет значения), как num() на странице."""
    if v in ("", None) or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


FEED_SORT = {     # те же сортировки, что были на странице; good приходит уже по оценке (потом по цене)
    "profit": lambda r: -(_fnum(r.get("profit_rub")) if _fnum(r.get("profit_rub")) is not None else -1e9),
    "desire": lambda r: -(r.get("s_desire") or 0), "liq": lambda r: -(r.get("s_liq") or 0),
    "price": lambda r: r.get("price_cny") or 0,
    "deal": lambda r: (-(_fnum(r.get("deal_pct")) or 0), -(r.get("total") or 0)),
}


def feed_query(good: list[dict], p: dict, order_ids: dict | None = None) -> dict:
    """Лента порциями: фильтры и сортировка — на сервере (раньше страница держала все вещи и фильтровала сама).
    p — параметры запроса /feed: фильтры как на странице, sort, offset, part=outs (раздел «вне каталога»), order."""
    t = lambda k: str(p.get(k) or "").strip()
    q, mn, minp, maxp, prof = low(t("q")), _fnum(t("min")), _fnum(t("minp")), _fnum(t("maxp")), _fnum(t("prof"))
    cat_, brand, tier, risk, src = t("cat"), t("brand"), t("tier"), t("risk"), t("src")
    flag = lambda k: t(k) in ("1", "true")
    deal, fresh, like, yhb, pos, size, known, men = (flag(k) for k in ("deal", "fresh", "like", "yhb", "pos", "size", "known", "men"))
    oids = (order_ids or {}).get(t("order")) if t("order") else None

    def ok(r):
        pr = _fnum(r.get("profit_rub"))
        return ((not cat_ or r.get("category") == cat_) and (not brand or r.get("brand") == brand)
                and (not tier or r.get("tier") == tier) and (mn is None or (r.get("total") or 0) >= mn)
                and (minp is None or (r.get("price_cny") or 0) >= minp) and (maxp is None or (r.get("price_cny") or 0) <= maxp)
                and (prof is None or (pr is not None and pr >= prof)) and (not pos or (pr is not None and pr > 0))
                and (not risk or (r.get("fake_risk") == "низкий" if risk == "low" else r.get("fake_risk") != "высокий"))
                and (not men or r.get("gender") != "ж")
                and (not src or r.get("src") == src) and (not yhb or "验货宝" in str(r.get("tags", "")))
                and (not size or (_fnum(r.get("size_bonus")) or 0) >= 5) and (not known or not str(r.get("category", "")).startswith("Тип"))
                and (not q or q in low(str(r.get("title", "")))) and (not fresh or bool(r.get("status")))
                and (not like or r.get("mark") == "like") and (not deal or bool(_fnum(r.get("deal_pct"))))
                and (oids is None or r["id"] in oids))
    lst = [r for r in good if ok(r)]
    if oids is not None:
        lst.sort(key=lambda r: -oids[r["id"]])
    elif t("sort") == "fresh":      # новизна — по точному времени первой встречи (при равном — по оценке)
        lst.sort(key=lambda r: str(r.get("first_ts") or r.get("first_seen") or ""), reverse=True)
    elif t("sort") in FEED_SORT:
        lst.sort(key=FEED_SORT[t("sort")])
    main = [r for r in lst if not r.get("outside")]
    outs = [r for r in lst if r.get("outside")]
    try:
        off = max(int(float(t("offset") or 0)), 0)
    except ValueError:
        off = 0
    part = outs if t("part") == "outs" else main
    out = {"items": [slim_row(r) for r in part[off:off + FEED_PAGE]], "total": len(part), "offset": off}
    if t("part") != "outs" and off == 0:     # первая страница: счётчики строки «показано…», начало «вне каталога», находки
        profits = [v for v in (_fnum(r.get("profit_rub")) for r in main) if v is not None and v > 0]
        unseen = sorted((r for r in good if r.get("outside") and not r.get("has_desc") and not r.get("mark")),
                        key=lambda r: -(r.get("total") or 0))
        out.update(outs=[slim_row(r) for r in outs[:24]], outs_total=len(outs),
                   base=sum(1 for r in good if not r.get("outside")), good_total=len(good),
                   n75=sum(1 for r in main if (r.get("total") or 0) >= 75), nnew=sum(1 for r in main if r.get("status") == "новое"),
                   best=round(max(profits)) if profits else 0,
                   unseen=[[r["id"], r.get("url", "")] for r in unseen[:300]])
    return out
TOTOP_HTML = ui_text("totop.html")     # кнопка «наверх» после двух экранов прокрутки
REPORT_HTML = ui_text("report.html").replace("</body>", TOTOP_HTML + "</body>")


SIDE_CSS = """<style>
:root{--bg:#eef0ed;--card:#fbfcfa;--fg:#151816;--muted:#646b66;--line:#d9ddd8;--soft:#e4e8e3;--accent:#24584a;--good:#237a45;--bad:#b23a2e}
@media (prefers-color-scheme:dark){:root{--bg:#0f1211;--card:#171b19;--fg:#e7ebe8;--muted:#929a95;--line:#2a312d;--soft:#212724;--accent:#86c4ae;--good:#6fd09a;--bad:#f07a6c;color-scheme:dark}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 "IBM Plex Sans","Segoe UI",system-ui,sans-serif}
main{max-width:1100px;margin:0 auto;padding:20px max(16px,env(safe-area-inset-left,0px)) 48px;display:grid;gap:18px}
h1{font:700 22px "Unbounded","Arial Black",sans-serif;margin:0}h2{font-size:15px;margin:0 0 8px}
.note{color:var(--muted);font-size:13px;margin:0}
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px}
.tile b{display:block;font:600 22px "IBM Plex Mono",Consolas,monospace;font-variant-numeric:tabular-nums}.tile span{color:var(--muted);font-size:12px}
.box{background:var(--card);border:1px solid var(--line);border-radius:12px;overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{padding:7px 12px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{color:var(--muted);font-weight:600;font-size:12px}td.n{font-family:"IBM Plex Mono",Consolas,monospace;white-space:nowrap;font-variant-numeric:tabular-nums}
.c{display:inline-block;font-size:11.5px;padding:1px 8px;border-radius:999px;background:var(--soft);color:var(--muted);margin:1px 2px 1px 0}
.off{color:var(--muted)}pre{margin:0;padding:12px 14px;font:12px/1.5 "IBM Plex Mono",Consolas,monospace;white-space:pre-wrap}
/* ===== общий стиль, редакция 11.2026: стеклянные панели, заголовки с меткой, таблицы и поля в одном ключе ===== */
:root{--mid:#a26a07;--gold:#9c7410;--glass:rgb(255 255 255/.55);--glass-2:rgb(255 255 255/.75);--glass-brd:rgb(20 40 30/.10);--glass-hi:rgb(255 255 255/.85)}
@media (prefers-color-scheme:dark){:root{--mid:#e3a94a;--gold:#e2bd55;--glass:rgb(255 255 255/.035);--glass-2:rgb(255 255 255/.06);
 --glass-brd:rgb(255 255 255/.09);--glass-hi:rgb(255 255 255/.07)}}
body{background:radial-gradient(1000px 620px at 4% -12%,color-mix(in srgb,var(--accent) 20%,transparent),transparent 62%),
 radial-gradient(820px 520px at 100% 0%,color-mix(in srgb,var(--gold) 14%,transparent),transparent 60%),
 radial-gradient(900px 700px at 50% 118%,rgb(90 120 220/.12),transparent 62%),var(--bg);background-attachment:fixed;
 -webkit-font-smoothing:antialiased}
main{gap:20px}
h1{letter-spacing:.01em}
h2{display:flex;align-items:center;gap:10px;font:650 17px "Unbounded","Segoe UI Variable Display","Segoe UI",sans-serif;letter-spacing:.01em;margin:0 0 10px}
h2::before{content:"";flex:none;width:6px;height:18px;border-radius:3px;background:linear-gradient(180deg,var(--accent),color-mix(in srgb,var(--accent) 35%,transparent))}
main>section{scroll-margin-top:64px}
/* разделы — стеклянные панели (кроме «шапки» и подвала) */
main>section:not(.hero):not(.foot2):not(#now){background:linear-gradient(160deg,var(--glass-2),var(--glass) 60%);border:1px solid var(--glass-brd);
 border-radius:20px;padding:18px 20px;box-shadow:inset 0 1px 0 var(--glass-hi),0 10px 30px rgb(0 0 0/.12)}
@media (max-width:600px){main>section:not(.hero):not(.foot2):not(#now){padding:14px 12px;border-radius:16px}}
.box,.tile{background:linear-gradient(160deg,var(--glass-2),var(--glass) 60%);border:1px solid var(--glass-brd);border-radius:16px;
 box-shadow:inset 0 1px 0 var(--glass-hi)}
.tile b{letter-spacing:-.01em}
table{font-size:13px}th{font-size:11px;letter-spacing:.06em;text-transform:uppercase;font-weight:600}
tbody tr,table tr{transition:background .12s}table tr:hover td{background:color-mix(in srgb,var(--accent) 7%,transparent)}
td.n{color:var(--fg)}
input:not([type=checkbox]):not([type=radio]),select,textarea{font:inherit;font-size:13px;color:var(--fg);background:var(--glass-2);
 border:1px solid var(--glass-brd);border-radius:10px;padding:7px 11px;outline:none;transition:border-color .15s,box-shadow .15s}
input:not([type=checkbox]):not([type=radio]):focus,select:focus,textarea:focus{border-color:var(--accent);box-shadow:0 0 0 3px color-mix(in srgb,var(--accent) 22%,transparent)}
select option{background:var(--card);color:var(--fg)}
button{font:inherit}
main button:not(.c):not(.obc):not(.sv button):not(.rf){border-radius:999px}
.c{background:var(--glass-2);border:1px solid var(--glass-brd)}
a{color:var(--accent)}
details>summary{cursor:pointer}
::selection{background:color-mix(in srgb,var(--accent) 35%,transparent)}
@media (prefers-reduced-motion:no-preference){main>section{animation:sec-in .5s cubic-bezier(.2,.8,.2,1) both}
 main>section:nth-child(2){animation-delay:.04s}main>section:nth-child(3){animation-delay:.08s}main>section:nth-child(4){animation-delay:.12s}
 main>section:nth-child(5){animation-delay:.16s}main>section:nth-child(n+6){animation-delay:.2s}
 @keyframes sec-in{from{opacity:0;transform:translateY(12px)}}}
/* «Статус» перезагружается каждые 5 с — без въезда разделов, иначе страница мигает */
html:has(meta[http-equiv=refresh]) main>section{animation:none}
</style>""" + TOTOP_HTML


# ---------------------------------------------------------------- обучение по ★/✕
TASTE_REASONS = ("", "не мой стиль", "женское")   # скрытия, которые говорят о вкусе, а не о цене/подделке
LEARN_MIN = 4                                      # сколько похожих отметок нужно для подсказки
# слово-подсказка должно быть редким (не чаще чем в 2% всех объявлений) и в скрытых встречаться в 5+ раз чаще обычного:
# иначе в правила попадали «尺寸» (размер, 9% объявлений), «差包» (кусок «邮差包» — мессенджер), «图片», «干净»
LEARN_DF_MAX, LEARN_LIFT = 0.02, 5
RULE_TEMPLATES = {
    "женское": {"name": "женская линия", "reject": True, "words": []},
    "подделка": {"name": "признаки подделки", "reject": True, "words": []},
    "": {"name": "мои минусы", "desire": -15, "penalty": 10, "words": []},
}


def _tokens(text: str) -> set[str]:
    """Кандидаты в слова: латиница целиком, китайский кусками по 2–4 иероглифа (пробелов там нет)."""
    out = set(re.findall(r"[a-z][a-z0-9'\-]{2,}", text))
    for run in re.findall(r"[一-鿿]+", text):
        for n in (2, 3, 4):
            out.update(run[i:i + n] for i in range(len(run) - n + 1))
    return out


def _catalog_words(c) -> set[str]:
    words = set()
    def walk(x):
        if isinstance(x, str):
            words.add(low(x).strip())
        elif isinstance(x, list):
            for y in x:
                walk(y)
        elif isinstance(x, dict):
            for k, y in x.items():
                if k not in ("name", "_help", "_note"):
                    walk(y)
    walk(c)
    return {w for w in words if w}


def learn(db, rows: list[dict], a, cat: dict, prices: dict) -> dict:
    """Разбор ваших ★/✕: подсказки к каталогу. Сам каталог не меняет."""
    reasons = {}
    for iid, reason in db.execute("SELECT id, reason FROM marks_log WHERE mark='hide' ORDER BY ts"):
        reasons[iid] = reason or ""
    dismissed = {k for (k,) in db.execute("SELECT key FROM learn_dismissed")}
    brand_key = {b["name"]: k for k, b in cat["brands"].items()}
    marked = []
    for r in rows:
        if r.get("mark") not in ("like", "hide"):
            continue
        x = dict(r, mark="")
        ok, why = evaluate(x, a, cat, prices)    # скрытые иначе не оцениваются: смотрим, как их видит парсер
        x.update(_mark=r["mark"], _ok=ok, _why=why, _reason=reasons.get(r["id"], "") if r["mark"] == "hide" else "",
                 _text=low(f'{r.get("title", "")} {r.get("desc", "")}'))
        marked.append(x)
    liked = [x for x in marked if x["_mark"] == "like"]
    # «состояние», «не тот цвет» — про эту вещь, не про модель: в поиске слов-признаков не участвуют
    hidden = [x for x in marked if x["_mark"] == "hide" and x["_reason"] not in ITEM_ONLY_REASONS]
    marked = [x for x in marked if x["_mark"] == "like" or x in hidden]
    sugg = []

    # 1. слова, которые встречаются почти только в скрытых
    known = _catalog_words(cat)
    df, dh, dl = {}, {}, {}
    for r in rows:
        for t in _tokens(low(f'{r.get("title", "")} {r.get("desc", "")}')):
            df[t] = df.get(t, 0) + 1
    for x in marked:
        bucket = dh if x["_mark"] == "hide" else dl
        for t in _tokens(x["_text"]):
            bucket.setdefault(t, []).append(x)
    n_all, n_h = max(len(rows), 1), max(len(hidden), 1)
    cands, near = [], []
    for t, hs in dh.items():
        h, l = len(hs), len(dl.get(t, []))
        if l * 5 > h or t in known or any(t in k for k in known if len(k) >= len(t)):
            continue
        common = df.get(t, 0) / n_all > LEARN_DF_MAX or (h / n_h) / (df.get(t, 1) / n_all) < LEARN_LIFT
        if h < LEARN_MIN:
            if h >= 2 and not common:
                near.append((h, t))
            continue
        if common:
            continue
        cands.append((h, l, t, hs))
    chosen = []
    for h, l, t, hs in sorted(cands, key=lambda c: (-c[0], len(c[2]))):
        if any((t in c or c in t) and h <= ch for ch, _, c, _ in chosen):
            continue
        chosen.append((h, l, t, hs))
    for h, l, t, hs in chosen[:8]:
        why = {}
        for x in hs:
            why[x["_reason"]] = why.get(x["_reason"], 0) + 1
        top = max(why, key=why.get)
        tpl_key = top if top in ("женское", "подделка") else ""
        # один продавец (или сеть) пишет один текст — его куски («达黑» из «普拉达黑色») не признак;
        # правило-отсев («подделка», «женское») бьёт сильнее «моих минусов», ему нужно больше доказательств
        sellers = len({x.get("seller_id") or x.get("seller") or x["id"] for x in hs})
        need_h, need_s = (2 * LEARN_MIN, LEARN_MIN + 1) if tpl_key else (LEARN_MIN, 3)
        if h < need_h or sellers < need_s or l:
            continue
        rule = RULE_TEMPLATES[tpl_key]
        what = {"женское": "отсеивать как женское", "подделка": "отсеивать как подделку",
                "": f"снижать оценку (правило «{rule['name']}»: −15 к желанности, −10 к итогу)"}[tpl_key]
        reason_txt = ", ".join(f"{k or 'без причины'} {v}" for k, v in sorted(why.items(), key=lambda i: -i[1]))
        sugg.append({"key": f"word:{t}", "kind": "Слово",
                     "text": f"«{t}»: скрыто {h}, ★ {l}. Предлагаю {what}.",
                     "detail": f"Причины: {reason_txt}. Примеры: " + " · ".join(x["title"][:40] for x in hs[:3]),
                     "op": ["add_word", tpl_key, t]})

    # 2. модели, которые вы стабильно лайкаете или скрываете
    groups = {}
    for x in liked + [x for x in hidden if x["_reason"] in TASTE_REASONS]:
        if x.get("model") and x.get("brand") in brand_key:
            groups.setdefault((brand_key[x["brand"]], x["model"]), []).append(x)
    for (bk, mname), xs in groups.items():
        l = sum(1 for x in xs if x["_mark"] == "like")
        h = len(xs) - l
        if l + h < LEARN_MIN + 1:
            continue
        m = next((m for m in cat["brands"][bk].get("models", []) if m["name"] == mname), None)
        if not m:
            continue
        cur = m.get("desire", cat["brands"][bk]["desire"])
        new = int(clamp(round(cur + 20 * (l - h) / (l + h))))
        if abs(new - cur) >= 5:
            sugg.append({"key": f"model:{bk}:{mname}:{new}", "kind": "Модель",
                         "text": f"{cat['brands'][bk]['name']} · {mname}: ★ {l}, ✕ {h}. "
                                 f"{'Поднять' if new > cur else 'Снизить'} желанность {cur} → {new}?",
                         "detail": "Считаются только скрытия «не мой стиль», «женское» и без причины.",
                         "op": ["model_desire", bk, mname, new]})

    # 3. цены: все ★ заметно дороже порога — порог можно поднять и меньше видеть мусора
    pgroups = {}
    for x in liked:
        if x.get("brand") in brand_key and x.get("category"):
            ck = next((k for k, c in cat["categories"].items() if c["name"] == x["category"]), "")
            if ck:
                pgroups.setdefault((brand_key[x["brand"]], ck), []).append(x["price_cny"])
    for (bk, ck), ps in pgroups.items():
        if len(ps) < LEARN_MIN:
            continue
        b = cat["brands"][bk]
        cur = b.get("min_price", {}).get(ck, b.get("min_price", {}).get("*", cat["categories"][ck].get("min_price", 0)))
        lo = min(ps)
        if cur and lo >= cur * 1.3:
            new = int(lo * 0.85 // 50 * 50)
            sugg.append({"key": f"price:{bk}:{ck}:{new}", "kind": "Цена",
                         "text": f"{b['name']} · {cat['categories'][ck]['name']}: все ваши ★ ({len(ps)}) от ¥{lo:.0f}. "
                                 f"Поднять мин. цену ¥{cur:.0f} → ¥{new}?",
                         "detail": "Меньше дешёвых вещей и подделок в ленте; более дорогие не затрагиваются.",
                         "op": ["min_price", bk, ck, new]})

    # 4. бренды вне каталога, которые вы лайкаете: пора завести их в каталог
    ob = {}
    for x in liked:
        if x["_ok"] and x.get("outside_brand"):
            ob.setdefault(x["outside_brand"], []).append(x)
    for name, xs in ob.items():
        if len(xs) < 2:
            continue
        sugg.append({"key": f"brand:{name}", "kind": "Бренд",
                     "text": f"{name}: ★ {len(xs)} вне каталога. Добавить бренд в каталог?",
                     "detail": "Бренд переедет в основную ленту. Цены продажи и признаки подлинности по нему "
                               "соберёт Claude — напишите «проверь находки». Примеры: "
                               + " · ".join(x["title"][:40] for x in xs[:3]),
                     "op": ["add_brand", name]})

    sugg = [s for s in sugg if s["key"] not in dismissed]
    top = lambda xs: sum(1 for x in xs if x.get("total", 0) >= 75)
    errors = ([("Скрыто, но оценка высокая", x) for x in sorted(hidden, key=lambda x: -x.get("total", 0))
               if x.get("total", 0) >= 80 and x["_reason"] in TASTE_REASONS][:8]
              + [("★, но оценка низкая", x) for x in sorted(liked, key=lambda x: x.get("total", 0))
                 if x["_ok"] and x.get("total", 0) < 60][:8])
    # история (последняя отметка по каждой вещи, если она ещё действует), бренды и причины
    by_id = {x["id"]: x for x in marked}
    recent, seen_ids = [], set()
    for iid, mk, reason, ts in db.execute("SELECT id, mark, reason, ts FROM marks_log ORDER BY ts DESC LIMIT 400"):
        if iid in seen_ids or iid not in by_id or by_id[iid]["_mark"] != mk:
            continue
        seen_ids.add(iid)
        recent.append(dict(by_id[iid], _ts=ts, _reason=reason or ""))
        if len(recent) >= 40:
            break
    brands = {}
    for x in marked:
        b = brands.setdefault(x.get("brand") or "бренд не распознан", {"like": 0, "hide": 0})
        b[x["_mark"]] += 1
    why = {}
    for x in hidden:
        why[x["_reason"] or "без причины"] = why.get(x["_reason"] or "без причины", 0) + 1
    near = [(h, t) for h, t in sorted(near, key=lambda n: (-n[0], len(n[1]))) if not any(t in c[2] for c in chosen)]
    return {"liked": len(liked), "hidden": len(hidden),
            "acc_like": round(100 * top(liked) / len(liked)) if liked else None,
            "acc_hide": round(100 * top(hidden) / len(hidden)) if hidden else None,
            "suggestions": sugg, "errors": errors, "recent": recent,
            "brands": sorted(brands.items(), key=lambda kv: -(kv[1]["like"] + kv[1]["hide"])),
            "reasons": sorted(why.items(), key=lambda kv: -kv[1]), "near": near[:12],
            "sellers": len(SELLER_BLACKLIST), "rules": [dict(x, off=x["key"] in LEARNED["off"]) for x in LEARNED["rules"]],
            "unmarked": sum(1 for r in rows if not r.get("mark"))}


def apply_suggestion(c: dict, op: list):
    kind = op[0]
    if kind == "add_word":
        tpl = RULE_TEMPLATES[op[1]]
        rule = next((m for m in c.setdefault("modifiers", []) if m["name"] == tpl["name"]), None)
        if rule is None:
            rule = json.loads(json.dumps(tpl))
            c["modifiers"].append(rule)
        if op[2] not in rule["words"]:
            rule["words"].append(op[2])
    elif kind == "model_desire":
        for m in c["brands"][op[1]]["models"]:
            if m["name"] == op[2]:
                m["desire"] = op[3]
    elif kind == "min_price":
        c["brands"][op[1]].setdefault("min_price", {})[op[2]] = op[3]
    elif kind == "add_brand":
        o = c["outside"]
        b = next(b for b in o["interesting"] if b["name"] == op[1])
        o["interesting"].remove(b)
        key = re.sub(r"[^a-z0-9]+", "_", low(b["name"])).strip("_") or "brand"
        while key in c["brands"]:
            key += "_"
        c["brands"][key] = {"name": b["name"], "aliases": b["words"], "desire": b["desire"], "liquidity": 60,
                            "ambassador": [], "min_price": {"*": o.get("min_price", 500)}, "models": [], "variants": []}
        if b.get("sell_rub"):
            c["brands"][key]["sell_rub"] = b["sell_rub"]


def learn_page(res: dict) -> str:
    e = html_escape
    pct = lambda v: "—" if v is None else f"{v}%"
    n = res["liked"] + res["hidden"]
    head = (f'<div class="tiles"><div class="tile"><b>{res["liked"]}</b><span>★ интересно</span></div>'
            f'<div class="tile"><b>{res["hidden"]}</b><span>✕ скрыто</span></div>'
            f'<div class="tile"><b>{pct(res["acc_like"])}</b><span>★ с оценкой 75+ (чем выше, тем лучше)</span></div>'
            f'<div class="tile"><b>{pct(res["acc_hide"])}</b><span>✕ с оценкой 75+ (чем ниже, тем лучше)</span></div></div>')
    if res["suggestions"]:
        cards = "".join(
            f'<div class="box" style="padding:12px 14px;display:grid;gap:6px"><div><span class="c">{e(s["kind"])}</span> {e(s["text"])}</div>'
            f'<div class="note">{e(s["detail"])}</div><div style="display:flex;gap:8px">'
            f'<button class="b go" onclick="act(\'apply\',\'{e(s["key"])}\')">Применить</button>'
            f'<button class="b" onclick="act(\'dismiss\',\'{e(s["key"])}\')">Не то</button></div></div>'
            for s in res["suggestions"])
    else:
        cards = (f'<p class="note">Пока подсказок нет. Ставьте ★ и ✕ в ленте (у ✕ можно выбрать причину). '
                 f'Подсказка появляется, когда набирается {LEARN_MIN}+ похожих отметок; обычно хватает 30–50 отметок.</p>')
    errs = "".join(f'<tr><td>{e(t)}</td><td class=n>{x.get("total", "")}</td><td>{e(x.get("brand", ""))} {e(x.get("model", ""))}</td>'
                   f'<td>{e(x["title"][:70])}</td><td><a href="{e(x.get("url", ""))}" target="_blank">открыть</a></td></tr>'
                   for t, x in res["errors"])
    # прогресс: сколько осталось до следующих подсказок
    near = "".join(f'<div class="nr"><span>«{e(t)}»</span><i style="--p:{100 * h // LEARN_MIN}%"></i><small>{h} из {LEARN_MIN}</small></div>'
                   for h, t in res.get("near", []))
    mx = max([v["like"] + v["hide"] for _, v in res.get("brands", [])] or [1])
    brands = "".join(f'<tr><td>{e(b)}</td><td class=n>{v["like"]}</td><td class=n>{v["hide"]}</td>'
                     f'<td style="width:45%"><span class="bb"><i class="l" style="width:{100 * v["like"] // mx}%"></i>'
                     f'<i class="h" style="width:{100 * v["hide"] // mx}%"></i></span></td></tr>'
                     for b, v in res.get("brands", [])[:15])
    reasons = "".join(f'<span class="c">{e(k)}: {v}</span>' for k, v in res.get("reasons", []))
    rules = "".join(f'<tr{" style=opacity:.5" if x["off"] else ""}><td>{e(x["text"])}</td><td class=n>{e(x["note"])}</td><td>'
                    + (f'<button class="b sm" onclick="act(\'restore\',\'{e(x["key"])}\')">вернуть</button>' if x["off"]
                       else f'<button class="b sm" onclick="act(\'dismiss\',\'{e(x["key"])}\')">забыть</button>')
                    + '</td></tr>' for x in res.get("rules", []))
    hist = "".join(f'<tr><td>{"<span class=lk>★</span>" if x["_mark"] == "like" else "<span class=hd>✕</span>"}</td>'
                   f'<td class=n>{e(x["_ts"][5:16].replace("T", " "))}</td><td>{e(x.get("brand", ""))}</td>'
                   f'<td>{e(x["title"][:60])}</td><td>{e(x["_reason"])}</td><td class=n>¥{x.get("price_cny", 0):.0f}</td>'
                   f'<td><button class="b sm" onclick="undo(\'{e(x["id"])}\',this)">отменить</button></td></tr>'
                   for x in res.get("recent", []))
    body = (f'<h1>Обучение</h1><p class="note">Подсказки по вашим ★ и ✕. Каталог меняется только по кнопке «Применить» '
            f'(с резервной копией и пересчётом ленты).</p>{head}'
            + f'<p><a class="b go" href="/rate" style="text-decoration:none;display:inline-block">Быстрая разметка →</a> '
              f'<span class="note">по одной вещи, клавишами; чем больше отметок, тем точнее оценка под ваш вкус</span></p>'
            + (f'<p class="note">Продавцов реплик в чёрном списке: <b>{res.get("sellers", 0)}</b> — список во вкладке «Статус».</p>')
            + (f'<section><h2>Выучено из скрытий</h2><p class="note">После {RULE_MIN} скрытий с причиной «{PRICE_REASON}» '
               f'(одна модель) или «{SIZE_REASON}» (один размер) похожие вещи отсеиваются сами — их видно в «Отсеяно». '
               f'★ дороже порога или с этим размером правило ослабляет.</p><div class="box"><table>'
               f'<tr><th>Правило</th><th></th><th></th></tr>{rules}</table></div></section>' if rules else "")
            + f'<section><h2>Подсказки</h2><div style="display:grid;gap:10px">{cards}</div></section>'
            + (f'<section><h2>Скоро появятся подсказки</h2><p class="note">Слова, которые часто встречаются в скрытых вами вещах. '
               f'Когда наберётся {LEARN_MIN}, предложу правило.</p><div class="box" style="padding:10px 14px;display:grid;gap:8px">{near}</div></section>' if near else "")
            + (f'<section><h2>Ваши отметки по брендам</h2><div class="box"><table><tr><th>Бренд</th><th>★</th><th>✕</th><th></th></tr>{brands}</table></div>'
               f'<div class="reasons" style="margin-top:10px">{reasons}</div></section>' if brands else "")
            + (f'<section><h2>Где оценка расходится с вами</h2><div class="box"><table><tr><th></th><th>Балл</th><th>Модель</th>'
               f'<th>Название</th><th></th></tr>{errs}</table></div></section>' if errs else "")
            + (f'<section><h2>Последние отметки</h2><p class="note">Ошиблись — нажмите «отменить», вещь вернётся в ленту.</p>'
               f'<div class="box"><table><tr><th></th><th>Когда</th><th>Бренд</th><th>Название</th><th>Причина</th><th>Цена</th><th></th></tr>{hist}</table></div></section>' if hist else "")
            + (f'<p class="note">Всего отметок: {n}.</p>' if n else ""))
    js = """<style>.b{font:inherit;font-size:13px;padding:6px 12px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--fg);cursor:pointer}
.b.go{background:var(--accent);border-color:var(--accent);color:var(--card);font-weight:600}.b.sm{padding:3px 9px;font-size:12px}#m{font-size:13px}
.tile{transition:transform .15s,box-shadow .15s}.tile:hover{transform:translateY(-2px);box-shadow:0 8px 24px color-mix(in srgb,var(--accent) 18%,transparent)}
.nr{display:grid;grid-template-columns:minmax(80px,180px) 1fr 54px;gap:10px;align-items:center;font-size:13px}
.nr i{display:block;height:6px;border-radius:3px;background:linear-gradient(90deg,var(--accent) var(--p),var(--soft) 0)}.nr small{color:var(--muted)}
.bb{display:flex;height:8px;border-radius:4px;overflow:hidden;background:var(--soft)}.bb .l{background:#e2bd55}.bb .h{background:var(--bad)}
.lk{color:#e2bd55;font-weight:700}.hd{color:var(--bad);font-weight:700}.reasons{display:flex;flex-wrap:wrap;gap:6px}
@media (prefers-reduced-motion:no-preference){.nr i,.bb i{animation:grow .8s cubic-bezier(.2,.7,.2,1) both}@keyframes grow{from{clip-path:inset(0 100% 0 0)}}
 .box,.tile{animation:rise .45s ease-out both}@keyframes rise{from{opacity:0;transform:translateY(8px)}}}</style>
<script>function undo(id,el){el.disabled=true;fetch("/mark",{method:"POST",headers:{"Content-Type":"application/json","X-Goofish-UI":"1"},body:JSON.stringify({id:id,mark:""})})
.then(r=>{if(!r.ok)throw 0;const tr=el.closest("tr");tr.style.opacity=.35;el.textContent="отменено"}).catch(()=>{el.disabled=false;document.getElementById("m").textContent="Приложение не отвечает"})}
function act(a,k){document.getElementById("m").textContent="…";fetch("/learn/"+a,{method:"POST",headers:{"Content-Type":"application/json","X-Goofish-UI":"1"},body:JSON.stringify({key:k})})
.then(r=>r.json().then(j=>({ok:r.ok,j}))).then(({ok,j})=>{if(!ok){document.getElementById("m").textContent=(j.errors||["ошибка"]).join("; ");return}location.reload()})
.catch(()=>document.getElementById("m").textContent="Приложение не отвечает")}</script>"""
    return f'<!doctype html><meta charset="utf-8"><title>Обучение</title>{SIDE_CSS}{js}<main><div id="m" class="note"></div>{body}</main>'


RATE_FIELDS = ("id", "brand", "model", "variant", "category", "title", "price_cny", "profit_rub", "total", "tier", "size",
               "condition", "fake_risk", "image", "url", "posted")


def rate_queue(good: list[dict], n: int = 60) -> list[dict]:
    """Вещи для быстрой разметки: неотмеченные, с фото и спорной оценкой 50–90 (там парсер чаще расходится с вами),
    по одной из каждой группы «бренд + модель» по кругу — чтобы отметки покрыли весь каталог, а не один бренд."""
    pool = sorted((r for r in good if not r.get("mark") and r.get("image") and 50 <= (r.get("total") or 0) <= 90),
                  key=lambda r: abs(r["total"] - 72))
    groups: dict[tuple, list[dict]] = {}
    for r in pool:
        groups.setdefault((r.get("brand"), r.get("model") or r.get("category")), []).append(r)
    out: list[dict] = []
    while groups and len(out) < n:
        for k in list(groups):
            out.append(groups[k].pop(0))
            if not groups[k]:
                del groups[k]
            if len(out) >= n:
                break
    return [{f: r.get(f, "") for f in RATE_FIELDS} for r in out]


RATE_HTML = ui_text("rate.html")


def rate_page(items: list[dict]) -> str:
    js = lambda v: json.dumps(v, ensure_ascii=False).replace("</", "<\\/")
    return (RATE_HTML.replace("__CSS__", SIDE_CSS).replace("__ITEMS__", js(items))
            .replace("__WHY__", js([w for w in HIDE_REASONS if w != JUNK_REASON])))


EDITOR_HTML = ui_text("editor.html")


def search_query(brand: dict, model: dict | None = None) -> str:
    """Запрос для Goofish: бренд и слова модели, по-китайски, если есть («普拉达 尼龙 邮差»)."""
    cjk = lambda ws: next((w for w in ws if re.search(r"[一-鿿]", w)), ws[0] if ws else "")
    parts = [cjk(brand.get("aliases") or [brand["name"]])]
    if model:
        parts += [cjk(g) for g in model.get("all", [])] if model.get("all") else [cjk(model.get("words") or [])]
    return " ".join(p for p in parts if p)


BRAND_CN_FILE = Path(__file__).with_name("brand_cn.json")
CHAN_MIN_POSTS, CHAN_MAX = 3, 10    # «из каналов» в «Мои поиски»: бренд в 3+ постах за 4 месяца, не больше 10 поисков
CHAN_MAX_JP = 8                     # те же каналы -> поиски Mercari (бренд латиницей), не больше 8


def load_brand_cn() -> dict:
    """Китайские названия брендов для поиска на Goofish (brand_cn.json): {"brands": {бренд: иероглифы или ""}}."""
    try:
        d = json.loads(BRAND_CN_FILE.read_text(encoding="utf-8"))
    except Exception:
        d = {}
    return {"_help": d.get("_help", ""), "brands": dict(d.get("brands") or {}), "_gemini": list(d.get("_gemini") or [])}


def save_brand_cn(d: dict):
    BRAND_CN_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def brand_variants(row: dict, cat: dict, cn_map: dict) -> tuple[str, str | None]:
    """Строка «ходового» -> (латиница, иероглифы). Иероглифы: из brand_cn.json, иначе из слов бренда в каталоге;
    "" — продавцы пишут латиницей; None — ещё не известно (спросим Gemini)."""
    if row.get("in_cat"):
        b = next((b for b in cat["brands"].values() if b["name"] == row["brand"]), {})
        ws = [low(w) for w in b.get("aliases", [])]
    else:
        b = next((b for b in (cat.get("outside") or {}).get("interesting", []) if b["name"] == row["brand"]), {})
        ws = [low(w) for w in b.get("words", [])]
    lat = next((w for w in ws if re.search(r"[a-z]", w) and len(w) > 3), "") or low(row["q"])
    if row["brand"] in cn_map:
        return lat, cn_map[row["brand"]]
    return lat, next((w for w in ws if re.search(r"[一-鿿]", w)), None)


_MKT: dict = {"key": None, "idx": {}}


def market_index(db, cat: dict) -> dict:
    """Цены России из постов каналов и объявлений Avito/Oskelly за 4 месяца: (бренд, "m", модель) и (бренд, "c", тип) ->
    сколько объявлений, почём просят, сколько «продано», откуда. Пересчёт — только при новых постах."""
    key = db.execute("SELECT COUNT(*), MAX(seen) FROM market").fetchone()
    if key == _MKT["key"]:
        return _MKT["idx"]
    groups: dict = {}
    for r in market_rows(db, cat):
        for k in ((r["brand"], "m", r["model"]) if r["model"] else None, (r["brand"], "c", r["ckey"]) if r["ckey"] else None):
            if k:
                groups.setdefault(k, []).append(r)
    idx = {}
    for k, rs in groups.items():
        ps = sorted(r["price_rub"] for r in rs)
        idx[k] = {"n": len(rs), "lo": ps[0], "hi": ps[-1], "med": int(statistics.median(ps)), "sold": sum(r["sold"] for r in rs),
                  "src": sorted({r["source"].split(":", 1)[-1] for r in rs})}
    _MKT.update(key=key, idx=idx)
    return idx


def market_note(r: dict, cat: dict, idx: dict) -> dict:
    """Строка «Россия: просят …» для вещи ленты: сначала по модели, иначе по бренду и типу; и насколько каталог
    расходится с рынком (если больше чем на 30%)."""
    bk = next((k for k, b in cat["brands"].items() if b["name"] == r.get("brand")), None)
    if not bk:
        return {}
    m = idx.get((bk, "m", r.get("model"))) if r.get("model") else None
    m = m if m and m["n"] >= 2 else None
    by = "model" if m else "brand"
    if not m:   # по бренду и типу — только если объявлений хватает: иначе пара дорогих новых вещей всё искажает
        m = idx.get((bk, "c", r.get("ckey") or ""))
        if not m or m["n"] < 5:
            return {}
    k_ = lambda v: f"{v / 1000:.0f}" if v >= 10000 else f"{v / 1000:.1f}".replace(".", ",")
    span = f"{k_(m['lo'])}–{k_(m['hi'])} тыс. ₽" if m["lo"] != m["hi"] else f"{k_(m['lo'])} тыс. ₽"
    note = (f"просят {span}" + (f", медиана {k_(m['med'])} тыс." if m["n"] > 2 else "")
            + f" · {m['n']} объявл." + (f" · {m['sold']} продано" if m["sold"] else "")
            + ("" if by == "model" else f" (по бренду: {r.get('category', '')})"))
    sell = r.get("sell_rub") if isinstance(r.get("sell_rub"), (int, float)) else 0
    # сравнение с каталогом — только по той же модели: по бренду в одну кучу попадают и кошельки, и дорожные сумки
    diff = round(100 * (sell / m["med"] - 1)) if by == "model" and sell and m["med"] and m["n"] >= 3 else 0
    return {"ru_mkt": note, "ru_src": ", ".join(m["src"][:4]), "ru_by": by, "ru_diff": diff if abs(diff) >= 30 else ""}


CAT_CN = {"bag": "包", "shoes": "鞋", "sneakers": "鞋", "outerwear": "外套", "eyewear": "眼镜"}


def best_spelling(words: list[str], default: str, qst: dict) -> str:
    """Написание бренда, которое на деле лучше работает в ваших поисках (больше вещей проходит в ленту).
    «鲍里斯» — это просто «Борис» (и медвежонок Миффи), продавцы пишут «boris bidjan»; без статистики — default."""
    seen = [(s[1] / s[0], w) for w in words for s in [qst.get(w)] if s and s[0] >= 30]
    return max(seen)[1] if seen else default


def similar_query(r: dict, cat: dict, qst: dict | None = None) -> str:
    """Запрос Goofish для кнопки «Похожее» в карточке: бренд и модель; без модели — бренд и тип вещи
    (слово типа из названия объявления, иначе общее: 包, 鞋, 外套…). Бренд — в лучшем по вашим поискам написании."""
    qst = qst or {}
    b = next((b for b in cat["brands"].values() if b["name"] == r.get("brand")), None)
    if b:
        base = search_query(b)
        q = best_spelling([low(w) for w in b.get("aliases", [])], base, qst)
        m = next((m for m in b.get("models", []) if m["name"] == r.get("model")), None)
        if m:
            return f"{q} {search_query(b, m)[len(base):].strip()}".strip()
    else:
        ob = next((o for o in (cat.get("outside") or {}).get("interesting", [])
                   if o["name"] in (r.get("outside_brand"), r.get("brand"))), None)
        ws = [low(w) for w in (ob or {}).get("words", [])]
        q = next((w for w in ws if re.search(r"[一-鿿]", w)), "") or next((w for w in ws if len(w) > 3), "")
        if not q:
            return ""
        q = best_spelling(ws, q, qst)
    title = low(r.get("title", ""))
    words = [w for w in (cat["categories"].get(r.get("ckey") or "", {}).get("words") or []) if re.search(r"[一-鿿]", w)]
    kind = next((w for w in words if w in title), "") or CAT_CN.get(r.get("ckey") or "", "")
    return f"{q} {kind}".strip()


def _title_words(r: dict) -> set:
    """Слова названия для похожести: латиница от 3 букв и китайский парами иероглифов."""
    t = low(r.get("title", ""))
    out = {w for w in re.findall(r"[a-z][a-z0-9'-]{2,}", t)}
    for run in re.findall(r"[一-鿿]+", t):
        out |= {run[i:i + 2] for i in range(len(run) - 1)}
    return out


def like_similar(good: list[dict], cat: dict, limit: int = 12) -> list[dict]:
    """«Похоже на ваши ★»: вещи ленты, ближе всего к отмеченным звёздочкой — бренд, модель, линии, тип, цена, слова.
    Не больше 3 вещей одной модели, чтобы раздел не забивала одна сумка."""
    liked = [r for r in good if r.get("mark") == "like"]
    if not liked:
        return []
    # лента между пересчётами обычно та же — тогда и ответ тот же (сам расчёт ~0,5 с)
    key = (limit, tuple((r["id"], r.get("mark"), r.get("price_cny"), r.get("total"), r.get("model")) for r in good))
    if _LIKE_SIM["key"] == key:     # те же вещи, но строки ленты — новые (в них свежие цены России и пр.)
        by = {r["id"]: r for r in good}
        return [dict(x, r=by[x["r"]["id"]]) for x in _LIKE_SIM["res"]]
    _LIKE_SIM.update(key=key, res=_like_similar(good, liked, cat, limit))
    return _LIKE_SIM["res"]


_LIKE_SIM: dict = {"key": None, "res": []}


def _like_similar(good: list[dict], liked: list[dict], cat: dict, limit: int) -> list[dict]:
    mods = {g_["name"] for g_ in cat.get("modifiers", [])}     # служебные пометки («мои минусы») — не линии
    var = lambda r: set(filter(None, str(r.get("variant", "")).split(", "))) - mods
    lt = [(r, _title_words(r), var(r)) for r in liked]
    out = []
    for c in good:
        if c.get("mark"):
            continue
        ct, cv = _title_words(c), var(c)
        best = None
        for r, rt, rv in lt:
            why, s = [], 0.0
            if c.get("brand") == r.get("brand"):
                s += 3
                why.append("тот же бренд")
                if c.get("model") and c.get("model") == r.get("model"):
                    s += 4
                    why[-1] = "тот же бренд и модель"
            if cv & rv:
                s += 2 * min(len(cv & rv), 2)
                why.append(", ".join(sorted(cv & rv))[:40])
            if c.get("ckey") and c.get("ckey") == r.get("ckey"):
                s += 2
            if r.get("price_cny") and 0.5 <= c.get("price_cny", 0) / r["price_cny"] <= 1.5:
                s += 1
                why.append("близкая цена")
            j = len(ct & rt) / max(len(ct | rt), 1)
            s += 6 * j
            if j >= 0.25:
                why.append("похожее название")
            if best is None or s > best[0]:
                best = (s, r, why)
        if best and best[0] >= 6:
            out.append((best[0], c, best[1], best[2]))
    out.sort(key=lambda x: (-x[0], -x[1].get("total", 0)))
    res, per = [], {}
    for _, c, r, w in out:
        k = (c.get("brand"), c.get("model") or c.get("ckey"))
        if per.get(k, 0) >= 3:
            continue
        per[k] = per.get(k, 0) + 1
        res.append({"r": c, "like": f'{r.get("brand", "")} {r.get("model") or r.get("category", "")}'.strip(), "why": "; ".join(w)})
        if len(res) >= limit:
            break
    return res


_HOT: dict = {"key": None, "rows": []}


def market_hot(db, cat: dict, days: int = 120) -> list[dict]:
    """«Ходовое в каналах»: какие бренды (каталог + «интересные вне каталога») и модели чаще всего продают в Telegram-каналах
    за days дней — с ценой в рублях, числом «продано» и готовым запросом для Goofish. Пересчёт — только при новых постах."""
    key = db.execute("SELECT COUNT(*), MAX(seen) FROM market").fetchone()
    if key == _HOT["key"]:
        return _HOT["rows"]
    since = (datetime.now() - timedelta(days=days)).isoformat()
    outside = [(b["name"], [low(w) for w in b.get("words", [])]) for b in (cat.get("outside") or {}).get("interesting", [])]
    groups: dict = {}
    seen = set()
    for source, url, ts, text in db.execute("SELECT source, url, ts, text FROM market WHERE ts >= ? ORDER BY ts DESC", (since,)):
        h = re.sub(r"\W+", "", low(text))[:200]
        if h in seen or has_any(low(text), MARKET_BUY):
            continue
        seen.add(h)
        p = parse_market_post(text, cat, shop=source.startswith("shop:"))
        prices = post_prices(text)
        if p:
            b = cat["brands"][p["brand"]]
            model = next((m for m in b.get("models", []) if m["name"] == p["model"]), None)
            k = (b["name"], p["model"])
            g = groups.setdefault(k, {"brand": b["name"], "model": p["model"], "in_cat": True, "q": search_query(b, model)})
            price, sold = p["price_rub"], p["sold"]
        else:   # бренд вне каталога из списка «интересных»: модель и прибыль не считаем, только сколько и почём
            t = low(text.split("\n", 1)[0][:200] + " " + text)
            hit = next((n for n, ws in outside if hits(t, ws)), None)
            if not hit:
                continue
            ws = dict(outside)[hit]
            g = groups.setdefault((hit, ""), {"brand": hit, "model": "", "in_cat": False,
                                              "q": next((w for w in ws if re.search(r"[a-z]", w) and len(w) > 3), ws[0])})
            price, sold = (prices[0] if len(prices) == 1 else 0), bool(has_any(low(text), MARKET_SOLD))
        g.setdefault("posts", []).append({"price": price, "sold": sold, "ch": source.split(":", 1)[-1], "url": url})
    rows = []
    for g in groups.values():
        ps = g.pop("posts")
        if len(ps) < 2:
            continue
        pr = sorted(x["price"] for x in ps if x["price"])
        rows.append({**g, "n": len(ps), "sold": sum(x["sold"] for x in ps), "med": int(statistics.median(pr)) if pr else 0,
                     "lo": pr[0] if pr else 0, "hi": pr[-1] if pr else 0,
                     "ch": sorted({x["ch"] for x in ps}), "url": next((x["url"] for x in ps if x["url"].startswith("http")), "")})
    rows.sort(key=lambda r: (-r["n"], -r["sold"], -r["med"]))
    _HOT.update(key=key, rows=rows[:16])
    return _HOT["rows"]


# «Тихие» поиски: другие написания бренда, латиница, опечатки, прозвища — объявления, которые почти никто не находит.
# Шлак в ленту по ним не попадёт: каждое объявление проходит те же фильтры, а прозвище само брендом не считается
# (в тексте должно быть настоящее имя из каталога). Опечатки добавлены в написания бренда в каталоге.
QUIET_NICK = {"golden_goose": ["小脏鞋"]}
QUIET_TYPOS = {"margeila", "margiella", "balenciga", "crome hearts", "louis vuiton"}


def quiet_searches(cat: dict) -> list[dict]:
    out = []
    for key, b in sorted(cat["brands"].items(), key=lambda kv: -kv[1].get("desire", 0)):
        main = low(search_query(b))
        flat = lambda s: s.replace(" ", "").replace("·", "")
        cands = []
        for al in b.get("aliases", []) + QUIET_NICK.get(key, []):
            w = low(al)
            # короткая латиница (bv, lv, bbs — слишком общие) и то, что поиск по главному имени и так находит, — не тихие
            short = len(w) < 2 or (w.isascii() and len(w) < 4 and not re.search(r"\d", w))
            if short or main in w or w in main or any(flat(c) == flat(w) for c in cands):
                continue
            cands.append(w)
        for w in cands:
            if any(c != w and c in w for c in cands):      # «maison margiela» найдётся и по «margiela»
                continue
            nick = w in QUIET_NICK.get(key, [])
            why = ("опечатка: в обычном поиске такие объявления не видны" if w in QUIET_TYPOS
                   else "прозвище: в ленту пройдёт, только если в тексте есть имя бренда" if nick
                   else "латиницей: продавцы без китайского названия" if w.isascii()
                   else "другое написание бренда: так ищут редко")
            out.append({"q": w, "name": b["name"], "hint": "опечатка" if w in QUIET_TYPOS else "прозвище" if nick else "", "why": why})
    return out[:30]


QUIET_DEFAULT = list(range(1, 8))   # 01–07 по Пекину = 20:00–02:59 по Москве, пока своих данных мало
QUIET_MIN_N = 500                    # столько объявлений с известным временем публикации нужно, чтобы считать самим


def quiet_hours(db, state: dict) -> tuple[list[int], str]:
    """Тихие часы по Пекину: когда на Goofish выкладывают меньше 40% от среднего за час. Считается по времени
    публикации объявлений за 2 недели, не чаще раза в 30 минут. Автопоиск в эти часы ходит втрое реже."""
    cached = state.get("quiet_hours")
    if cached and time.time() - cached[0] < 1800:
        return cached[1], cached[2]
    cn = timezone(timedelta(hours=8))
    since = (datetime.now() - timedelta(days=14)).isoformat(timespec="seconds")
    by = [0] * 24
    for (p,) in db.execute("SELECT json_extract(data, '$.posted') FROM items WHERE last_seen >= ?", (since,)):
        try:
            t = datetime.fromisoformat(p)
        except (TypeError, ValueError):
            continue
        if t.isoformat() >= since:
            by[t.astimezone(cn).hour] += 1
    n = sum(by)
    if n < QUIET_MIN_N:
        hours, note = QUIET_DEFAULT, f"по умолчанию (своих данных пока {n} из {QUIET_MIN_N})"
    else:
        hours = [h for h in range(24) if by[h] < 0.4 * n / 24] or QUIET_DEFAULT
        note = f"по {n} объявлениям за 2 недели"
    state["quiet_hours"] = (time.time(), hours, note)
    return hours, note


def hour_ranges(hours: list[int]) -> str:
    """[1, 2, …, 7] -> «01–08»; [23, 0, 1] -> «23–02»."""
    hs = sorted(set(hours))
    runs: list[list[int]] = []
    for h in hs:
        if runs and runs[-1][1] == h:
            runs[-1][1] = h + 1
        else:
            runs.append([h, h + 1])
    if len(runs) > 1 and runs[0][0] == 0 and runs[-1][1] == 24:    # через полночь
        runs[0][0] = runs.pop()[0]
    return ", ".join(f"{a:02d}–{b % 24:02d}" for a, b in runs) or "нет"


def latin_name(b: dict, words_key: str = "aliases") -> str:
    """Название бренда латиницей (для Mercari): первое латинское написание длиннее 3 букв, без иероглифов."""
    return next((low(w) for w in b.get(words_key, []) if re.search(r"[a-z]", low(w)) and len(w) > 3
                 and not re.search(r"[一-鿿]", w)), "")


SLOW_DAYS, SLOW_EVERY, SLOW_MIN_RUNS = 3, 4, 6


def slow_searches(state: dict, items: list[dict] | None = None) -> set:
    """Поиски, которые автопоиск сам проверяет реже — раз в SLOW_EVERY круга: за SLOW_DAYS дня не принесли ни одной
    вещи с оценкой 70+. Не трогаем: закреплённые вами («всегда каждый круг»), «раз в 3 круга», продавцов, поиски
    моложе SLOW_DAYS дней и те, что за этот сеанс открывались меньше SLOW_MIN_RUNS раз (после простоя нужен шанс).
    Возвращает ключи skey(запрос, площадка)."""
    c = state.get("slow_cache")
    if c and c[0] == state.get("built_at") and time.time() - c[2] < 120:
        return c[1]
    cut = (datetime.now() - timedelta(days=SLOW_DAYS)).isoformat()
    hit = set()
    for r in state.get("feed") or []:
        if (r.get("total") or 0) >= 70 and (r.get("first_ts") or "") >= cut:
            for q in str(r.get("queries", "")).split("; "):
                if q:
                    hit.add(skey(q, r.get("site", "")))
    runs, since_cut, out = state.get("queries", {}), (date.today() - timedelta(days=SLOW_DAYS)).isoformat(), set()
    for x in (items if items is not None else load_searches()["items"]):
        if x.get("seller") or x.get("pin") or x.get("rare"):
            continue
        qs = [v for v in (x["q"], x.get("alt", "")) if v]
        if any(skey(v, x.get("site", "")) in hit for v in qs) or (x.get("since") or "9") > since_cut \
                or sum((runs.get(v) or {}).get("pages", 0) for v in qs) < SLOW_MIN_RUNS:
            continue
        out.add(skey(x["q"], x.get("site", "")))
    state["slow_cache"] = (state.get("built_at"), out, time.time())
    return out


def query_junk(qstats: dict, q: str) -> bool:
    """Тот же признак «мало толку», что на «Главной»: 30+ объявлений и меньше 5% прошло в ленту."""
    s = qstats.get(q)
    return bool(s and s[0] >= 30 and s[1] / s[0] < 0.05)


def search_suggestions(state: dict, cat: dict) -> dict:
    """Подсказки под строкой «Мои поиски»: недавние запросы, советы, выгодное в ленте, линии из ленты.
    Пересчитываются по ленте при каждом открытии «Главной» и раз в 2 минуты на ней. Не советуем то, что уже
    пробовали с «мало толку», и модели, которые в ленте часто оказываются подозрительными."""
    fmt = lambda v: f"{v:,.0f}".replace(",", " ")
    qst = state.get("qstats", {})
    # уже добавленные в «Мои поиски» отсеиваем до отбора лучших — иначе списки пустеют по мере добавления
    saved = load_searches()["items"]
    mine = {low(v) for x in saved if x.get("site") != "mercari" for v in (x["q"], x.get("alt", "")) if v}
    jp_have = {low(x["q"]) for x in saved if x.get("site") == "mercari"}
    recent = [{"q": q, **({"site": "mercari"} if v.get("site") == "mercari" else {})}
              for q, v in list(state.get("queries", {}).items())[::-1]
              if not q.startswith(("?", "рекомендации", "профиль продавца", "карточка"))
              and low(q) not in (jp_have if v.get("site") == "mercari" else mine)][:8]
    by_name = {b["name"]: b for b in cat["brands"].values()}
    # статистика ленты по бренду+модели: сколько вещей, сколько с оценкой 75+, сколько подозрительных
    stat: dict = {}
    lines: dict = {}
    for r in state.get("by_id", {}).values():
        b = by_name.get(r.get("brand"))
        if not b or r.get("mark") == "hide":
            continue
        g = stat.setdefault((b["name"], r.get("model") or ""), {"b": b, "n": 0, "top": 0, "risky": 0, "p": 0, "t": 0})
        sus = r.get("fake_risk") not in (None, "", "низкий") or bool(r.get("sim_text"))
        p, t = r.get("profit_rub"), r.get("total", 0)
        g.update(n=g["n"] + 1, top=g["top"] + (t >= 75), risky=g["risky"] + sus, t=max(g["t"], t),
                 p=max(g["p"], p if isinstance(p, int) else 0))
        if t >= 75 and not sus:     # линии/вариации, которые чаще всего дают сильные вещи
            for vn in filter(None, str(r.get("variant", "")).split(", ")):
                v = next((v for v in b.get("variants", []) if v["name"] == vn), None)
                if v and v.get("words") and v.get("desire", 0) > 0:     # «спорная расцветка» и т.п. не советуем
                    lv = lines.setdefault((b["name"], vn), {"b": b, "v": v, "n": 0, "p": 0})
                    lv.update(n=lv["n"] + 1, p=max(lv["p"], p if isinstance(p, int) else 0))
    risky = lambda g: g is not None and g["n"] >= 3 and g["risky"] / g["n"] >= 0.3
    ok_q = lambda q, seen: q not in seen and low(q) not in seen and not query_junk(qst, q)
    # советую: дорогие в перепродаже модели каталога, с поправкой на то, сколько сильных вещей они уже дали в ленте;
    # модели с порогом от подделок (Carryall, Neverfull…) и «подозрительные по ленте» не советуем
    picks, per_brand, seen = [], {}, set(mine)
    scored = []
    for b in cat["brands"].values():
        for m in b.get("models", []):
            sell = m.get("sell_rub") or (b.get("sell_rub") or {}).get(m.get("category"), 0)
            g = stat.get((b["name"], m["name"]))
            if sell and not m.get("min_price") and not risky(g):
                boost = 1 + 0.15 * min((g or {}).get("top", 0), 6)
                scored.append((sell * b.get("desire", 50) * b.get("liquidity", 50) / 1e4 * boost, b, m, sell, g))
    for _, b, m, sell, g in sorted(scored, key=lambda x: -x[0]):
        q = search_query(b, m)
        if per_brand.get(b["name"], 0) >= 2 or not ok_q(q, seen):
            continue
        seen.add(q)
        per_brand[b["name"]] = per_brand.get(b["name"], 0) + 1
        top = (g or {}).get("top", 0)
        picks.append({"q": q, "hint": f"~{fmt(sell)} ₽", "name": f'{b["name"]} · {m["name"]}',
                      "why": f"примерная цена перепродажи в РФ: {fmt(sell)} ₽" + (f"; в ленте уже {top} с оценкой 75+" if top else "")})
        if len(picks) >= 10:
            break
    # выгодно в ленте: бренд+модель с лучшей прибылью или высокой оценкой среди того, что вы уже видели
    feed = []
    for (bn, mn), g in sorted(stat.items(), key=lambda x: (-x[1]["p"], -x[1]["t"])):
        if risky(g) or not (g["p"] > 0 or g["t"] >= 80):
            continue
        m = next((m for m in g["b"].get("models", []) if m["name"] == mn), None)
        q = search_query(g["b"], m)
        if not ok_q(q, seen):
            continue
        seen.add(q)
        feed.append({"q": q, "hint": f'+{fmt(g["p"])} ₽' if g["p"] else f'оценка {g["t"]}',
                     "name": f'{bn} · {mn or "любая модель"}',
                     "why": f'в ленте {g["n"]} шт., из них 75+: {g["top"]}; лучшая прибыль {fmt(g["p"])} ₽'})
        if len(feed) >= 10:
            break
    # из ленты: бренд + линия/вариация (архив, Linea Rossa, цвет…), которая чаще всего встречается у сильных вещей
    line = []
    for (bn, vn), lv in sorted(lines.items(), key=lambda x: (-x[1]["n"], -x[1]["p"])):
        cjk = next((w for w in lv["v"]["words"] if re.search(r"[一-鿿]", w)), lv["v"]["words"][0])
        q = f'{search_query(lv["b"])} {cjk}'
        if lv["n"] < 2 or cjk in search_query(lv["b"]) or not ok_q(q, seen):
            continue
        seen.add(q)
        line.append({"q": q, "hint": f'{lv["n"]} шт. 75+', "name": f"{bn} · {vn}",
                     "why": f'в ленте {lv["n"]} вещей этой линии с оценкой 75+' + (f'; лучшая прибыль {fmt(lv["p"])} ₽' if lv["p"] else "")})
        if len(line) >= 10:
            break
    # «ходовое в каналах» с китайским вариантом запроса — «+ В автопоиск» добавит оба, автопоиск выберет лучший
    cn_map = load_brand_cn()["brands"]
    hot = [{**r, "lat": v[0], "cn": v[1] or ""} for r in state.get("market_hot", []) for v in [brand_variants(r, cat, cn_map)]]
    now = time.time()
    fresh = {q: round((now - t) / 60) for q, t in state.get("qnew_at", {}).items()}     # мин с последней новой вещи (ключ skey)
    trials = {k: {q: v[0] for q, v in t.items() if q not in ("since", "done")} for k, t in state.get("trials", {}).items()}
    # для Mercari: бренды каталога и «ходового» латиницей (японцы пишут бренд латиницей; на Mercari ищут так же)
    jp, jseen = [], set(jp_have)
    for b in sorted(cat["brands"].values(), key=lambda b: -b.get("desire", 50) * b.get("liquidity", 50)):
        q = latin_name(b)
        if not q or q in jseen:
            continue
        jseen.add(q)
        sells = [m.get("sell_rub") or 0 for m in b.get("models", [])] + list((b.get("sell_rub") or {}).values())
        top = max(sells, default=0)
        jp.append({"q": q, "site": "mercari", "name": b["name"], "hint": f"до ~{fmt(top)} ₽" if top else "",
                   "why": "бренд каталога: Щебень знает его модели и цены в РФ" + (f"; дорогие модели до ~{fmt(top)} ₽" if top else "")})
    for r in hot:
        q = r["lat"] if re.search(r"[a-z]", r["lat"]) and not re.search(r"[一-鿿]", r["lat"]) else ""
        if q and q not in jseen:
            jseen.add(q)
            jp.append({"q": q, "site": "mercari", "name": r["brand"], "hint": f'{r["n"]} постов',
                       "why": f'ходовое в каналах: {r["n"]} постов за 4 месяца' + (" (бренд вне каталога)" if not r["in_cat"] else "")})
    return {"recent": recent, "pick": picks, "feed": feed, "line": line, "jp": jp[:16], **sellers_overview(state), "hot": hot,
            "fresh": fresh, "trials": trials,
            "quiet": [x for x in quiet_searches(cat) if not query_junk(qst, x["q"]) and low(x["q"]) not in mine],
            "slow": sorted(slow_searches(state, saved))}


def sellers_overview(state: dict) -> dict:
    """«Мои продавцы» на «Главной»: за кем следит автопоиск и кандидаты — у кого в ленте больше всего выгодных вещей
    без риска подделки. Профиль открывается только по числовому id; в поиске id шифрованный — числовой появляется,
    когда вы открываете любую вещь продавца (или его профиль)."""
    smap, by_s = state.get("seller_map", {}), {}
    for r in state.get("by_id", {}).values():
        sid = str(r.get("seller_id") or "")
        if not sid or r.get("mark") == "hide":
            continue
        uid = str(r.get("seller_uid") or (sid if sid.isdigit() else smap.get(sid, "")))
        g = by_s.setdefault(uid or sid, {"uid": "", "nick": "", "n": 0, "ok": 0, "brands": {}, "url": "", "p": 0, "top": [],
                                         "site": r.get("site", "")})
        g["uid"] = g["uid"] or uid
        if r.get("seller") and not str(r["seller"]).startswith("продавец "):
            g["nick"] = r["seller"]
        g["n"] += 1
        p = r.get("profit_rub") if isinstance(r.get("profit_rub"), int) else 0
        if p > 0 and r.get("fake_risk") not in ("высокий", "средний"):
            g["ok"] += 1
            g["brands"][r.get("brand", "")] = g["brands"].get(r.get("brand", ""), 0) + 1
            g["top"].append((p, r.get("image", ""), r.get("url", "")))
            if p > g["p"]:
                g.update(p=p, url=r.get("url", ""))

    def card(g: dict, nick: str = "") -> dict:
        top = sorted(g["top"], key=lambda x: -x[0])[:4]
        return {"uid": g["uid"], "nick": nick or g["nick"] or "без ника", "url": g["url"], "n": g["n"], "ok": g["ok"], "p": g["p"],
                "site": g.get("site", ""),
                "brands": ", ".join(f"{b} {n}" for b, n in sorted(g["brands"].items(), key=lambda x: -x[1])[:3] if b),
                "imgs": [{"img": i, "url": u, "p": p} for p, i, u in top if i]}
    empty = {"uid": "", "nick": "", "n": 0, "ok": 0, "brands": {}, "url": "", "p": 0, "top": []}
    seen = state.get("seller_seen", {})
    followed = []
    for x in load_searches()["items"]:
        if x.get("seller"):
            c = card(by_s.get(x["seller"]) or dict(empty, uid=x["seller"]), x["q"].removeprefix("продавец "))
            followed.append({**c, "every": not x.get("rare"), "seen": seen.get(x["seller"]), "site": x.get("site", "")})
    have = {x["uid"] for x in followed}
    cands = [card(g) for g in sorted(by_s.values(), key=lambda g: (-g["ok"], -g["n"]))
             if g["ok"] >= 2 and not (g["uid"] and g["uid"] in have)][:12]
    return {"followed": followed, "sellers": cands}


PULSE_H, PULSE_STEP = 3, 300      # «Пульс» на «Главной»: последние 3 часа корзинами по 5 минут


def pulse_data(db, state: dict) -> dict:
    """Сколько новых вещей поймано (first_seen) за 3 часа по 5 минут — отдельно Goofish и Mercari, и находки уровня A
    точками. Считается при пересчёте ленты (новые вещи пересчитывают её не позже чем через 15 с)."""
    now = time.time()
    t0 = now - PULSE_H * 3600
    n = PULSE_H * 3600 // PULSE_STEP
    b = {"": [0] * n, "mercari": [0] * n}
    by_id, finds = state.get("by_id", {}), []
    since = datetime.fromtimestamp(t0).isoformat(timespec="seconds")
    for iid, fs, site in db.execute("SELECT id, first_seen, json_extract(data, '$.site') FROM items WHERE first_seen >= ?", (since,)):
        try:
            t = datetime.fromisoformat(fs).timestamp()
        except (TypeError, ValueError):
            continue
        s = "mercari" if site == "mercari" else ""
        b[s][max(0, min(int((t - t0) // PULSE_STEP), n - 1))] += 1
        r = by_id.get(iid)
        if r and (r.get("total") or 0) >= 75:
            p = r.get("profit_rub")
            finds.append({"id": iid, "t": round(t), "site": s, "img": r.get("image", ""), "url": r.get("url", ""),
                          "name": f'{r.get("brand", "")} · {r.get("model") or r.get("category", "")}',
                          "p": p if isinstance(p, int) else None,
                          "price": (f'¥{r.get("price_jpy") or 0:,.0f} JP' if s else f'¥{r.get("price_cny") or 0:,.0f}').replace(",", " ")})
    finds = sorted(sorted(finds, key=lambda x: -(x["p"] or 0))[:40], key=lambda x: x["t"])
    return {"t0": round(t0), "step": PULSE_STEP, "gf": b[""], "jp": b["mercari"], "finds": finds}


def stats_data(db, state: dict) -> dict:
    """«Аналитика» на «Главной»: воронка по площадкам и когда выкладывают вещи (тепловая карта по вашему времени).
    Считается в фоне (stats_bg в listen()) со своим соединением с базой: страница получает готовое сразу."""
    site = lambda s: "mercari" if s == "mercari" else ""
    z = lambda: {"": 0, "mercari": 0}
    seen, like, feed, top, bought, sold = z(), z(), z(), z(), z(), z()
    for s, m, n in db.execute("SELECT json_extract(data, '$.site'), mark, COUNT(*) FROM items GROUP BY 1, 2"):
        seen[site(s)] += n
        if m == "like":
            like[site(s)] += n
    for r in state.get("by_id", {}).values():
        k = site(r.get("site"))
        feed[k] += 1
        top[k] += (r.get("total") or 0) >= 75
    for st, s in db.execute("SELECT d.status, json_extract(i.data, '$.site') FROM deals d LEFT JOIN items i ON i.id = d.item_id"):
        bought[site(s)] += 1
        sold[site(s)] += st == "продано"
    funnel = [{"t": t, "gf": d[""], "jp": d["mercari"], "why": w} for t, d, w in (
        ("увидено", seen, "все объявления, которые прошли через Щебень"), ("в ленте", feed, "прошли фильтры: бренд, цена, подделки, шлак"),
        ("оценка 75+", top, "сильные вещи ленты: оценка 75 и выше"), ("★", like, "вы отметили звёздочкой"),
        ("куплено", bought, "записано во вкладку «Сделки»"), ("продано", sold, "сделки со статусом «продано»"))]
    # когда выкладывают: время публикации за 2 недели, по вашему часовому поясу
    since = time.time() - 14 * 86400
    rows: dict = {"Goofish": [0] * 24, "Mercari": [0] * 24}
    hour = lambda p: datetime.fromtimestamp(datetime.fromisoformat(p).timestamp())
    for s, p in db.execute("SELECT json_extract(data, '$.site'), json_extract(data, '$.posted') FROM items "
                           "WHERE last_seen >= ?", (datetime.fromtimestamp(since).isoformat(timespec="seconds"),)):
        try:
            t = hour(p)
        except (TypeError, ValueError):
            continue
        if t.timestamp() >= since:
            rows["Mercari" if s == "mercari" else "Goofish"][t.hour] += 1
    brands: dict = {}
    for r in state.get("by_id", {}).values():      # по брендам — только вещи из ленты (у отсеянных бренд не определён)
        try:
            t = hour(r.get("posted"))
        except (TypeError, ValueError):
            continue
        if t.timestamp() >= since and r.get("brand"):
            brands.setdefault(r["brand"], [0] * 24)[t.hour] += 1
    heat = [{"name": k, "site": "mercari" if k == "Mercari" else "", "h": v, "n": sum(v)} for k, v in rows.items() if sum(v)]
    heat += [{"name": k, "site": "brand", "h": v, "n": sum(v)} for k, v in sorted(brands.items(), key=lambda x: -sum(x[1]))[:8]]
    off = round(datetime.now().astimezone().utcoffset().total_seconds() / 3600)
    return {"funnel": funnel, "heat": heat, "tz": off, "hour": datetime.now().hour}


PICKS_FILE = Path(__file__).with_name("picks.json")


def load_picks() -> dict:
    """Подборка для «Главной»: {"title": "…", "ids": [id вещей ленты], "notes": {id: пометка}}. Файл кладёт Claude по вашей
    просьбе «найди…» — вещи открываются во вкладках Щебня, где вы уже вошли в аккаунт. Пустой или нет файла — раздела нет."""
    try:
        p = json.loads(PICKS_FILE.read_text(encoding="utf-8"))
        return {"title": str(p.get("title") or "Подборка")[:120], "ids": [str(i) for i in p.get("ids") or []][:60],
                "budget": int(max(to_float(p.get("budget")), 0)),      # бюджет покупателя: прибыль на карточках — запас до него
                "notes": {str(k): str(v)[:160] for k, v in (p.get("notes") or {}).items()}}
    except Exception:
        return {"title": "", "ids": [], "notes": {}, "budget": 0}


def home_live(state: dict) -> dict:
    """Цифры плиток и карточки «Лучшее»/«Новое» для «Главной»: она подтягивает их раз в 20 секунд без перезагрузки."""
    def c(r):
        p = r.get("profit_rub")
        return {"id": r["id"], "url": r.get("url", ""), "img": r.get("image", ""), "t": r.get("total", ""),
                "name": f'{r.get("brand", "")} · {r.get("model") or r.get("category", "")}', "price": r.get("price_cny", 0),
                "p": p if isinstance(p, int) else None}
    pb = (state.get("picks") or {}).get("budget", 0)
    return {"tiles": {"total": state.get("total", 0), "good": state.get("good", 0), "top": state.get("top", 0),
                      "new": state.get("new", 0), "best": state.get("best_profit", 0), "bl": len(SELLER_BLACKLIST)},
            "top": [c(r) for r in state.get("home_top", [])], "new": [c(r) for r in state.get("home_new", [])],
            "sim": [dict(c(x["r"]), why=f'похоже на ★ {x["like"]}' + (f': {x["why"]}' if x["why"] else ""))
                    for x in state.get("like_sim", [])], "pulse": state.get("pulse"),
            "picks": [dict(c(r), why=n) if not pb or not r.get("cost_rub") else
                      dict(c(r), p=round(pb - r["cost_rub"]), why=(n + " · " if n else "") + f'затраты {r["cost_rub"]:,.0f} ₽'.replace(",", " "))
                      for r, n in (state.get("picks") or {}).get("rows", [])],
            "picks_title": (state.get("picks") or {}).get("title", "")}


def home_page(state: dict, cat: dict) -> str:
    """Главный экран приложения: сводка, лучшие находки, быстрый поиск по брендам каталога."""
    e = html_escape
    fmt = lambda v: f"{v:,.0f}".replace(",", " ")
    ck = state.get("checker")
    ai = (f'{ck.status.get("today", 0)} из {ck.status.get("limit", "?")} сегодня' if ck and ck.status.get("enabled") else "выключена")
    # плитки и карточки рисует скрипт страницы из home_live(): цифры отсчитываются, новые карточки въезжают
    live = home_live(state)
    tiles = [("total", "в базе"), ("good", "в ленте"), ("top", "оценка 75+"), ("new", "новых за сеанс"),
             ("best", "лучшая прибыль"), ("bl", "продавцов реплик")]
    brands = sorted(cat["brands"].values(), key=lambda b: -b.get("desire", 0))
    chips = "".join(f'<a class="qs" href="https://www.goofish.com/search?q={quote(b["aliases"][0])}" target="_blank">{e(b["name"])}</a>' for b in brands)
    sug = search_suggestions(state, cat)
    gaps = "".join(
        f'<tr><td><b>{e(g["brand"])}</b> · {e(g["type"])}</td><td>{g["n"]}</td><td>{g["have"]} из {g["need"]}</td>'
        f'<td><a class="qs" href="https://www.avito.ru/rossiya?q={quote(g["q"])}" target="_blank">Искать на Avito</a></td></tr>'
        for g in state.get("mkt_gaps", []))
    gaps = (f"""<section id="s_gaps"><h2>Не хватает цен России</h2><p class="note">У этих вещей цена продажи — заглушка: своей цены
 в каталоге нет, а объявлений б/у в России собрано мало. Нажмите «Искать на Avito» и пролистайте выдачу — Щебень сам прочитает
 открытую страницу. Когда наберётся 8 объявлений, цена продажи станет рыночной.</p>
 <table class="gaps"><tr><th>Бренд и тип</th><th>Вещей в ленте</th><th>Объявлений России</th><th></th></tr>{gaps}</table></section>"""
            if gaps else "")
    hour = datetime.now().hour
    hello = "Доброй ночи" if hour < 6 else "Доброе утро" if hour < 12 else "Добрый день" if hour < 18 else "Добрый вечер"
    _, quiet, qnote = state.get("quiet_hours") or (0, QUIET_DEFAULT, "по умолчанию")
    cn_now = datetime.now(timezone(timedelta(hours=8)))
    shift = round(datetime.now().astimezone().utcoffset().total_seconds() / 3600) - 8     # Пекин -> ваше время
    pace = (f'Сейчас в Китае {cn_now:%H:%M} — {"тихий час: автопоиск ходит втрое реже" if cn_now.hour in quiet else "обычный темп"}. '
            f'Тихие часы по Пекину: {hour_ranges(quiet)} (у вас {hour_ranges([(h + shift) % 24 for h in quiet])}), {qnote}.')
    body = f"""<section class="hero"><div><div class="brandline"><span class="lg">S</span><h1>Sheben<span class="ap">'</span>Store</h1></div>
 <p class="hello">{hello}. Ищем граали — без шлака и подделок.</p>
 <div class="acts"><a class="big go" href="#" onclick="nav('feed');return false">Открыть ленту →</a>
 <a class="big" href="https://www.goofish.com/" target="_blank">Goofish</a>
 <a class="big" href="#" onclick="nav('learn');return false">Обучение</a></div></div>
 <div class="tiles">{"".join(f'<div class="tile" data-k="{k}"><b>0</b><span>{t}</span></div>' for k, t in tiles)}</div></section>
<div class="wlive" id="wl" title="Что сейчас делает автопоиск (вкладка «Автопоиск»)"><span class="wdot"></span><div class="wtx"><b id="wl_s">Автопоиск выключен</b>
 <span id="wl_l"></span></div><span class="wcd" id="wl_c"></span><i class="wbar"><i id="wl_b"></i></i></div>
<nav class="snav" id="snav"><a href="#now">Сейчас</a><a href="#s_picks">Подборка</a><a href="#s_top">Лучшее</a><a href="#s_new">Новое</a><a href="#s_sim">Похоже на ★</a><a href="#orders">Заказы</a><a href="#searches">Поиски</a><a href="#sellers">Продавцы</a><a href="#hot">Ходовое</a><a href="#stats">Аналитика</a><a href="#quick">Быстрый поиск</a><a href="#s_gaps">Цены России</a></nav>
<section id="now"><div class="nowg">
 <div class="vbox ringbox"><div class="vh"><b>Круг автопоиска</b><span class="lgd"><i class="gf"></i>Goofish<i class="jp"></i>Mercari</span></div>
  <div class="rwrap"><svg id="ring" viewBox="-160 -160 320 320" role="img" aria-label="Круг автопоиска"></svg><div class="vtip" id="rtip" hidden></div></div>
  <p class="vnote">Сегменты — поиски круга по порядку: яркие пройдены, светится текущий. Лучик наружу — сколько новых вещей поиск принёс за сеанс.</p></div>
 <div class="vbox pulsebox"><div class="vh"><b>Пульс · 3 часа</b><span class="lgd"><i class="gf"></i>Goofish<i class="jp"></i>Mercari<i class="af"></i>оценка 75+</span>
  <span class="vsum" id="p_sum"></span></div>
  <div class="pchart" id="pchart"><svg id="pulse" role="img" aria-label="Новые вещи за 3 часа"></svg><div class="vtip" id="ptip" hidden></div></div>
  <p class="vnote">Сколько новых вещей поймано за каждые 5 минут. Точки — находки с оценкой 75+: наведите, чтобы увидеть вещь, нажмите — открыть.</p></div>
</div></section>
<section id="s_picks" hidden><h2 id="h_picks">Подборка</h2><p class="note">Вещи, которые вы просили найти. Открываются во вкладках
 Goofish и Mercari внутри Щебня. Проданные и ушедшие из ленты отсюда пропадают сами.</p><div class="hgrid" id="g_picks"></div></section>
<section id="s_top" hidden><h2>Лучшее в ленте</h2><div class="hgrid" id="g_top"></div></section>
<section id="s_new" hidden><h2>Новое за сеанс</h2><div class="hgrid" id="g_new"></div></section>
<section id="s_sim" hidden><h2>Похоже на ваши ★</h2><p class="note">Вещи из ленты, ближе всего к тем, что вы отметили звёздочкой:
 тот же бренд и модель, линии, тип вещи, цена, слова в названии. Чем больше ★, тем точнее.</p><div class="hgrid" id="g_sim"></div></section>
<section id="orders"><h2>Заказы клиентов</h2><p class="note">Ищете вещь под конкретного клиента: подходит тот же бренд/модель и размер, если при цене клиента
 остаётся прибыль (с доставкой и сборами). О новых подходящих вещах приходит уведомление; «размер?» — всё совпало, но размер в объявлении не указан.</p>
 <div id="ord" class="ords"></div>
 <div id="o_leads" class="olds"></div>
 <div class="sadd oldf"><textarea id="o_lead" rows="2" placeholder="Вставьте сюда текст объявления «ищу…» — Щебень сам разберёт бренды, размер и бюджет, останется проверить и нажать «Добавить заказ»"></textarea><button class="btn2" id="o_parse">Разобрать</button></div>
 <div class="sadd ordf"><input id="o_c" placeholder="клиент, например Иван"><select id="o_b" title="Можно выбрать несколько брендов по очереди"></select><span id="o_bl" class="obl"></span><select id="o_m"></select>
 <input id="o_w" placeholder="слова (необязательно)" title="Через пробел — нужны все слова. Варианты одного слова — через |, например 羽绒|ダウン|puffer"><input id="o_s" placeholder="размеры: M, L" title="Несколько размеров — через запятую">
 <input id="o_p0" type="number" min="0" step="500" placeholder="затраты от, ₽" title="Не показывать вещи, которые обходятся «под ключ» дешевле этой суммы. Можно не заполнять"><input id="o_p" type="number" min="0" step="500" placeholder="клиент платит до, ₽">
 <select id="o_g" title="Пол не ясен — вещь всё равно показывается"><option value="">любой пол</option><option value="м">мужское</option><option value="ж">женское</option></select>
 <input id="o_d" type="date" title="Нужно к дате. Сроки доставки задаются на вкладке «Расходы»: вещи, которые не успеют, помечаются">
 <input id="o_n" placeholder="заметка"><button class="btn2" id="o_add">Добавить заказ</button></div></section>
<section id="searches"><h2>Мои поиски <button class="rf" id="rf_s" title="Пересобрать поиски из каналов и начать следующий круг автопоиска сразу">↻ Обновить</button><span class="rfn" id="rf_s_n"></span></h2><p class="note">Два блока — две площадки: <b>Goofish</b> (Китай) и <b>Mercari</b> (Япония, через посредника).
 «Следить» справа вверху проходит все поиски по кругу; Mercari — вперемешку с Goofish (меняется в «в круге» ниже).
 У блока написано, когда автопоиск до него дойдёт, а поиск, который открыт сейчас, подсвечен зелёным.
 О новых выгодных вещах приходит уведомление: прибыль от порога на «Сделках» (если указано «до ¥» — только не дороже).
 Частоту проверки Щебень выбирает сам: поиск, который 3 дня не приносил вещей с оценкой 70+, идёт раз в 4 круга.<br>{e(pace)}</p>
 <div class="qrow" id="sv"></div>
 <div class="sadd"><label>где <select id="s_site" title="Где искать: Goofish (Китай) или Mercari (Япония, через посредника)"><option value="">Goofish</option><option value="mercari">Mercari</option></select></label><input id="s_q" placeholder="запрос, например stone island 毛衣"><input id="s_m" type="number" min="0" placeholder="до ¥">
 <button class="btn2" id="s_add">Добавить в Goofish</button><label>круг автопоиска каждые <input id="s_i" type="number" min="{WATCH_MIN_INTERVAL}"> мин</label>
 <label title="В каком порядке автопоиск проходит площадки за круг. «Вперемешку» — поиски Mercari равномерно между поисками Goofish; «только …» — другая площадка пропускается">в круге <select id="s_o">
 <option value="">вперемешку</option><option value="goofish_first">сначала Goofish</option><option value="mercari_first">сначала Mercari</option>
 <option value="goofish_only">только Goofish</option><option value="mercari_only">только Mercari</option></select></label></div>
 <div id="sug"></div></section>
<section id="hot"><h2>Ходовое в каналах <button class="rf" id="rf_h" title="Собрать посты Telegram-каналов сейчас, не дожидаясь ежедневного сбора">↻ Обновить</button><span class="rfn" id="rf_h_n"></span></h2><p class="note">Что чаще всего продают Telegram-каналы со вкладки «Цены» за 4 месяца:
 сколько постов, почём просят, сколько уже «продано». «+ Goofish» / «+ Mercari» добавляют готовый запрос в «Мои поиски» нужной площадки.
 Раз в день самые ходовые бренды добавляются и сами — в блоки «из каналов» у Goofish и у Mercari.
 Бренд «вне каталога» Щебень оценивает грубее — прибыль точнее, если добавить бренд на вкладке «Каталог».</p>
 <div class="hotg" id="hotg"></div></section>
<section id="sellers"><h2>Мои продавцы <span class="cnt" id="sl_n"></span></h2><p class="note">Их профили автопоиск открывает сам:
 обычных — раз в 3 круга (примерно раз в 10 минут), отмеченных «каждый круг» — каждые ~3 минуты. Добавить продавца: «+ Следить» у кандидата ниже,
 кнопка на панели Щебня в профиле или вещи продавца на Goofish, или откройте во вкладке Goofish свой список 关注 — подписки добавятся сами.</p>
 <div class="sgrid" id="sl"></div>
 <h3 class="h3">Кандидаты <button class="rf" id="rf_c" title="Пересчитать кандидатов по ленте">↻ Обновить</button><span class="note">у кого в ленте больше всего выгодных вещей без риска подделки</span></h3>
 <div class="sgrid" id="sc"></div></section>
<section id="stats"><h2>Аналитика</h2><p class="note">Где теряются вещи по дороге от «увидено» до «продано» и в какие часы продавцы
 выкладывают больше всего — по вашему времени. Считается по базе, обновляется вместе с лентой.</p>
 <div class="stg2"><div class="vbox"><div class="vh"><b>Воронка</b><span class="lgd"><i class="gf"></i>Goofish<i class="jp"></i>Mercari</span></div><div id="funnel" class="fnl"></div>
  <p class="vnote">Полосы в логарифмической шкале: иначе «продано» рядом с «увидено» не разглядеть. Справа — какая доля дошла с прошлого шага.</p></div>
 <div class="vbox"><div class="vh"><b>Когда выкладывают</b><span class="vsum" id="h_note"></span></div><div id="heat" class="heat"></div>
  <p class="vnote">Чем ярче клетка, тем больше объявлений выложено в этот час (за 2 недели, каждая строка — своя шкала). Обведён текущий час.</p></div></div>
</section>
<section id="quick"><h2>Быстрый поиск на Goofish</h2><div class="qrow">{chips}</div></section>
{gaps}
<section class="foot2"><span>Проверка фото ИИ: {e(ai)}</span><span>Продавцов реплик в чёрном списке: {len(SELLER_BLACKLIST)}</span></section>"""
    css = """<style>
body{background:radial-gradient(1000px 600px at 5% -10%,color-mix(in srgb,var(--accent) 22%,transparent),transparent 60%),
 radial-gradient(800px 500px at 100% 0%,rgb(226 189 85/.16),transparent 60%),radial-gradient(900px 700px at 50% 115%,rgb(90 120 220/.16),transparent 60%),var(--bg);background-attachment:fixed}
main{max-width:1500px}.hero{display:grid;grid-template-columns:minmax(0,1.1fr) minmax(0,1fr);gap:24px;align-items:center;padding:8px 0 10px}
@media (max-width:860px){.hero{grid-template-columns:1fr}}
.brandline{display:flex;align-items:center;gap:12px}.lg{display:grid;place-items:center;width:46px;height:46px;border-radius:14px;background:var(--accent);
 color:var(--bg);font:800 26px "Unbounded","Arial Black",sans-serif;box-shadow:0 8px 26px color-mix(in srgb,var(--accent) 45%,transparent)}
h1{font-size:34px}.ap{color:#e2bd55}.hello{color:var(--muted);font-size:15px;margin:10px 0 18px}
.acts{display:flex;flex-wrap:wrap;gap:10px}.big{padding:11px 18px;border-radius:999px;text-decoration:none;color:var(--fg);font-weight:600;
 background:rgb(255 255 255/.06);border:1px solid rgb(255 255 255/.12);box-shadow:inset 0 1px 0 rgb(255 255 255/.12);transition:transform .15s,background .15s}
.big:hover{transform:translateY(-2px);background:rgb(255 255 255/.1)}
.big.go{background:linear-gradient(180deg,color-mix(in srgb,var(--accent) 85%,white),var(--accent));color:var(--bg);border-color:transparent;
 box-shadow:inset 0 1px 0 rgb(255 255 255/.45),0 6px 20px color-mix(in srgb,var(--accent) 40%,transparent)}
.tiles{grid-template-columns:repeat(3,minmax(0,1fr))}.tile{background:rgb(255 255 255/.05);border-color:rgb(255 255 255/.10);border-radius:16px;
 box-shadow:inset 0 1px 0 rgb(255 255 255/.10)}
.oldf{align-items:stretch}.oldf textarea{flex:1;min-width:260px;background:rgb(255 255 255/.06);border:1px solid rgb(255 255 255/.12);border-radius:12px;padding:8px 12px;color:var(--fg);font:inherit;font-size:13px;resize:vertical}
.olds{margin-top:10px}.old{display:flex;gap:10px;align-items:center;font-size:13px;padding:6px 0;border-bottom:1px solid var(--line)}.old span{flex:1;color:var(--muted)}.old b{color:var(--fg)}
.old button{border:1px solid var(--line);background:none;color:var(--fg);border-radius:999px;padding:3px 10px;cursor:pointer}.late{color:var(--bad);text-decoration:none}
.ordf input[type=date],.ordf #o_g{background:rgb(255 255 255/.06);border:1px solid rgb(255 255 255/.12);border-radius:999px;padding:7px 12px;color:var(--fg);font:inherit;font-size:13px}
.obl{display:inline-flex;flex-wrap:wrap;gap:4px}.obc{border:1px solid var(--line);background:none;color:var(--fg);border-radius:999px;padding:3px 9px;font-size:12px;cursor:pointer}
.obc:hover{border-color:var(--bad);color:var(--bad)}
.gaps{border-collapse:collapse;margin-top:10px;font-size:13px}.gaps td,.gaps th{padding:6px 14px 6px 0;text-align:left;border-bottom:1px solid var(--line)}
.gaps th{color:var(--muted);font-weight:500}.gaps .qs{padding:4px 12px}
.qrow{display:flex;flex-wrap:wrap;gap:8px}.qs{padding:7px 14px;border-radius:999px;text-decoration:none;color:var(--fg);font-size:13px;
 background:rgb(255 255 255/.06);border:1px solid rgb(255 255 255/.12)}.qs:hover{border-color:var(--accent);color:var(--accent)}.qs.ghost{color:var(--muted)}
.hgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:12px}
.hc{position:relative;overflow:hidden;display:grid;gap:6px;text-decoration:none;color:var(--fg);background:rgb(255 255 255/.05);border:1px solid rgb(255 255 255/.10);border-radius:16px;
 padding:8px;box-shadow:inset 0 1px 0 rgb(255 255 255/.10);transition:transform .15s,border-color .15s}
.hc:hover{transform:translateY(-3px);border-color:color-mix(in srgb,var(--accent) 60%,transparent)}
.hi{position:relative;aspect-ratio:1;border-radius:11px;overflow:hidden;background:var(--soft)}.hi img{width:100%;height:100%;object-fit:cover}
.hi i{position:absolute;top:6px;left:6px;font:700 13px "Unbounded",sans-serif;font-style:normal;padding:4px 7px;border-radius:8px;background:var(--good);color:var(--bg)}
.hw{font-size:11px;color:#e2bd55;line-height:1.3;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.ht{font-size:12.5px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.hp{font:600 13px "IBM Plex Mono",monospace;display:flex;justify-content:space-between;gap:6px}
.pos{color:var(--good)}.neg{color:var(--bad)}.foot2{display:flex;flex-wrap:wrap;gap:8px 24px;color:var(--muted);font-size:12.5px}
.sv{display:inline-flex;align-items:center;gap:6px;padding-right:6px}.sv a{color:inherit;text-decoration:none}.sv a:hover{color:var(--accent)}
.sv i{font-style:normal;color:var(--muted);font-size:12px}.sv button{background:none;border:0;color:var(--muted);cursor:pointer;font-size:13px;padding:0 2px}
.sv button:hover{color:var(--accent)}.sv .dp{font-size:11px}.sv .dp.on{color:var(--accent)}.sv .qy{font-size:11px;padding-left:6px;border-left:1px solid rgb(255 255 255/.12)}
.sv.junk{opacity:.6;border-style:dashed}.sv.junk .qy{color:var(--bad)}
.jk{flex-basis:100%}.jk summary{cursor:pointer;color:var(--muted);font-size:12.5px;padding:6px 2px;list-style:none;width:max-content}
.jk summary::-webkit-details-marker{display:none}.jk summary::before{content:"▸ "}.jk[open] summary::before{content:"▾ "}
.jk summary:hover{color:var(--accent)}.jk .qrow{margin-top:6px}
button.qs{font:inherit;font-size:12.5px;cursor:pointer}.dj{color:var(--bad);border-style:dashed}
.ords{display:grid;gap:10px;grid-template-columns:repeat(auto-fill,minmax(min(100%,420px),1fr))}
.oc{display:grid;gap:8px;padding:12px;border-radius:16px;background:rgb(255 255 255/.05);border:1px solid rgb(255 255 255/.10)}
.ohd{display:flex;flex-wrap:wrap;gap:4px 10px;align-items:baseline;font-size:13px}.ohd>b{font-size:15px}.ohd i{color:var(--muted);font-size:12px}
.oms{display:flex;gap:8px;overflow-x:auto}.om{flex:none;width:96px;display:grid;gap:2px;text-decoration:none;color:var(--fg);font-size:11.5px}
.om img{width:96px;height:96px;object-fit:cover;border-radius:10px;background:var(--soft)}.om b{color:var(--good);font-size:12px}
.oft{display:flex;flex-wrap:wrap;gap:12px;align-items:center;font-size:12.5px}.oft a{color:var(--accent)}
.oft button{margin-left:auto;background:none;border:1px solid rgb(255 255 255/.15);color:var(--muted);border-radius:999px;padding:3px 10px;cursor:pointer;font:inherit}
.oft button:hover{color:var(--bad);border-color:var(--bad)}
.ordf select{background:rgb(255 255 255/.06);border:1px solid rgb(255 255 255/.12);border-radius:999px;padding:7px 12px;color:var(--fg);font:inherit;font-size:13px}
.ordf select option{background:var(--card);color:var(--fg)}#o_s{width:80px}#o_p{width:150px}.sadd{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:12px;color:var(--muted);font-size:12.5px}
.sadd input{background:rgb(255 255 255/.06);border:1px solid rgb(255 255 255/.12);border-radius:999px;padding:7px 12px;color:var(--fg);font:inherit;font-size:13px}
.sg{display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin-top:12px}.sg>span{color:var(--muted);font-size:11.5px;flex-basis:100%}
.sg>span b{color:var(--fg);font-size:12.5px;margin-right:8px}
.sgc{border:1px dashed rgb(255 255 255/.18);background:none;color:var(--fg);border-radius:12px;padding:5px 12px;font:inherit;font-size:12.5px;cursor:pointer;text-align:left}
.sgc em{display:block;font-style:normal;color:var(--muted);font-size:11px;margin-bottom:1px}
.sgc:hover{border-color:var(--accent);color:var(--accent)}.sgc i{font-style:normal;color:var(--good);margin-left:6px;font-size:11.5px}
#s_site{background:var(--soft);border:1px solid var(--line);border-radius:999px;padding:7px 10px;color:var(--fg);font:inherit}
.lg2.jp{background:color-mix(in srgb,#e0526b 18%,transparent);color:#e0526b}#s_q{width:280px}#s_m{width:90px}#s_i{width:60px;padding:4px 8px}#s_o{background:var(--soft);border:1px solid var(--line);border-radius:999px;padding:4px 8px;color:var(--fg);font:inherit}
.sblk{display:grid;gap:8px;padding:10px 12px 12px;border-radius:14px;border:1px solid rgb(255 255 255/.08);background:rgb(255 255 255/.03)}
.sblk.jp{border-color:color-mix(in srgb,#e0526b 35%,transparent)}.sbh{display:flex;flex-wrap:wrap;gap:4px 12px;align-items:baseline}
.sbh b{font-size:14.5px}.sblk.jp .sbh b{color:#e0526b}.eta{font-size:12.5px;color:var(--accent)}.sblk.jp .eta{color:#e0526b}
.sv.now{border-color:var(--good);box-shadow:0 0 0 2px color-mix(in srgb,var(--good) 35%,transparent)}
.stg{font-style:normal;font-size:10.5px;font-weight:600;padding:0 6px;border-radius:999px;margin-right:6px;background:rgb(255 255 255/.08);color:var(--muted)}
.stg.jp{background:color-mix(in srgb,#e0526b 20%,transparent);color:#e0526b}
#s_site.flash{border-color:var(--accent);box-shadow:0 0 0 3px color-mix(in srgb,var(--accent) 30%,transparent)}
.hadd.jp{background:#e0526b}.hadd.jp.done{background:none;border-color:color-mix(in srgb,#e0526b 50%,transparent);color:#e0526b}
.hadd+.hadd{margin-left:0}
.mn{border:1px solid color-mix(in srgb,#e0526b 45%,transparent);background:none;color:#e0526b;border-radius:999px;padding:0 7px;font:inherit;font-size:11px;cursor:pointer}
.rr{border:1px solid var(--line);background:none;color:var(--muted);border-radius:999px;padding:0 7px;font:inherit;font-size:11px;cursor:pointer}.rr.on{color:var(--mid);border-style:dashed}.rr.pin{color:var(--accent);border-color:color-mix(in srgb,var(--accent) 55%,transparent)}.rr.slow{color:#e2bd55;border-style:dashed;border-color:rgb(226 189 85/.5)}.btn2{border:0;border-radius:999px;padding:8px 14px;background:var(--accent);color:var(--bg);font:inherit;font-weight:600;cursor:pointer}
/* «Сейчас»: кольцо автопоиска и «Пульс»; «Аналитика»: воронка и часы */
.nowg{display:grid;grid-template-columns:minmax(0,340px) minmax(0,1fr);gap:14px}@media (max-width:900px){.nowg{grid-template-columns:1fr}}
.vbox{position:relative;display:grid;gap:8px;align-content:start;padding:12px 14px;border-radius:16px;background:rgb(255 255 255/.04);
 border:1px solid rgb(255 255 255/.09);box-shadow:inset 0 1px 0 rgb(255 255 255/.08);min-width:0}
.vh{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 12px}.vh b{font-size:14px}.vsum{margin-left:auto;font-size:12px;color:var(--muted)}
.lgd{display:inline-flex;align-items:center;gap:5px;font-size:11.5px;color:var(--muted)}.lgd i{width:9px;height:9px;border-radius:3px;margin-left:6px}
.lgd i.gf{background:var(--accent)}.lgd i.jp{background:#e0526b}.lgd i.af{background:#e2bd55;border-radius:50%}
.vnote{margin:0;font-size:11.5px;color:var(--muted);line-height:1.4}
.rwrap{position:relative;width:100%;max-width:310px;margin:0 auto}#ring{width:100%;height:auto;display:block;overflow:visible}
.rbg{fill:none;stroke:rgb(255 255 255/.08);stroke-width:14}
.rs{transition:opacity .5s,filter .5s;cursor:default}.rs.gf{fill:var(--accent)}.rs.jp{fill:#e0526b}.rs.next{opacity:.22}.rs.done{opacity:.85}
.rs.junk.next{opacity:.12}.rs.now{opacity:1;filter:drop-shadow(0 0 6px currentColor)}.rs.gf.now{color:var(--accent)}.rs.jp.now{color:#e0526b}
.ry{transition:y 1s cubic-bezier(.2,.8,.2,1),height 1s cubic-bezier(.2,.8,.2,1)}.ry.gf{fill:var(--accent)}.ry.jp{fill:#e0526b}.ry{opacity:.75}
#rptr{transition:transform 1.1s cubic-bezier(.3,.9,.3,1)}#rptr circle{fill:var(--fg)}
.rc1{font:700 22px "Unbounded",sans-serif;fill:var(--fg);text-anchor:middle}.rc2,.rc3,.rc4{font-size:12.5px;fill:var(--muted);text-anchor:middle}
.rc3{font:600 13px "IBM Plex Mono",monospace;fill:var(--fg)}.rc4{fill:#e0526b;font-size:12px}
.rs:hover{opacity:1}
.pchart{position:relative;min-height:200px}#pulse{width:100%;height:200px;display:block;overflow:visible}
.pa{transition:d .9s cubic-bezier(.2,.8,.2,1)}.pa.gf{fill:url(#g_gf)}.pa.jp{fill:url(#g_jp)}
.pl{fill:none;stroke-width:2;transition:d .9s cubic-bezier(.2,.8,.2,1)}.pl.gf{stroke:var(--accent)}.pl.jp{stroke:#e0526b}
.pg{stroke:rgb(255 255 255/.07);stroke-width:1}.pt{font-size:10.5px;fill:var(--muted)}.pnow{stroke:var(--good);stroke-dasharray:3 3}
.pf{fill:#e2bd55;stroke:var(--bg);stroke-width:1.5;cursor:pointer;transition:r .2s}.pf:hover{r:8}
.pf.jp{fill:#ff8fa3}.pdot{fill:var(--good)}
.vtip{position:absolute;z-index:5;pointer-events:none;min-width:150px;max-width:230px;padding:8px 10px;border-radius:12px;background:var(--card);
 border:1px solid color-mix(in srgb,var(--accent) 45%,transparent);box-shadow:0 10px 26px rgb(0 0 0/.35);font-size:12px;line-height:1.35}
.vtip img{display:block;width:100%;aspect-ratio:1;object-fit:cover;border-radius:8px;margin-bottom:6px;background:var(--soft)}
.vtip b{display:block;font-size:12.5px}.vtip .pos{color:var(--good);font-weight:700}.vtip i{font-style:normal;color:var(--muted)}
.stg2{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.3fr);gap:14px}@media (max-width:1000px){.stg2{grid-template-columns:1fr}}
.fnl{display:grid;gap:7px}.fr{display:grid;grid-template-columns:86px minmax(0,1fr) minmax(0,1fr) 54px;gap:8px;align-items:center;font-size:12.5px}
.fr.fh{font-size:11px;color:var(--muted)}.ft{color:var(--fg)}.fc{text-align:right;color:var(--muted);font:600 11.5px "IBM Plex Mono",monospace}
.fb{position:relative;height:22px;border-radius:7px;background:rgb(255 255 255/.05);overflow:hidden}
.fb i{position:absolute;left:0;top:0;bottom:0;width:0;border-radius:7px;transition:width 1.1s cubic-bezier(.2,.8,.2,1)}
.fb i.gf{background:linear-gradient(90deg,color-mix(in srgb,var(--accent) 55%,transparent),var(--accent))}
.fb i.jp{background:linear-gradient(90deg,color-mix(in srgb,#e0526b 55%,transparent),#e0526b)}
.fnl.on .fb i{width:var(--w)}.fb b{position:relative;padding-left:8px;line-height:22px;font:600 12px "IBM Plex Mono",monospace;color:var(--fg)}
.heat{display:grid;grid-template-columns:110px repeat(24,minmax(0,1fr)) 46px;gap:3px;align-items:center;font-size:11px}
.hl{color:var(--fg);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:12px}.hl.jp{color:#e0526b}.hl.gf{color:var(--accent)}
.hx{color:var(--muted);text-align:center;font-size:10px}.hn{color:var(--muted);text-align:right;font:600 10.5px "IBM Plex Mono",monospace}
.hc3{aspect-ratio:1;border-radius:4px;background:var(--accent);opacity:0;transform:scale(.6);transition:opacity .6s,transform .6s}
.hc3.jp{background:#e0526b}.hc3.br{background:#e2bd55}.heat.on .hc3{opacity:var(--o);transform:none}
.hc3.cur{outline:2px solid var(--fg);outline-offset:1px}
@media (prefers-reduced-motion:reduce){.pa,.pl,.ry,#rptr,.fb i,.hc3,.rs{transition:none}}
@media (prefers-reduced-motion:no-preference){.pdot{animation:wpulse 2s ease-in-out infinite}.pf.fresh{animation:pop .7s cubic-bezier(.2,.9,.3,1.25) both}}
/* пояснения разделов свёрнуты под «?» у заголовка */
.hintw{position:relative;display:inline-block;margin-left:8px;vertical-align:middle}
.hintb{width:20px;height:20px;border-radius:50%;border:1px solid rgb(255 255 255/.2);background:none;color:var(--muted);font:600 12px/1 inherit;cursor:help;padding:0}
.hintb:hover,.hintw.open .hintb{color:var(--accent);border-color:var(--accent)}
.hintbox{display:none;position:absolute;left:-8px;top:calc(100% + 8px);z-index:40;width:min(460px,calc(100vw - 48px));padding:10px 12px;border-radius:12px;
 background:var(--card);border:1px solid color-mix(in srgb,var(--accent) 40%,transparent);box-shadow:0 12px 30px rgb(0 0 0/.4);
 font-size:12.5px;font-weight:400;line-height:1.45;color:var(--fg);text-transform:none;letter-spacing:0;white-space:normal;text-align:left}
.hintw:hover .hintbox,.hintw.open .hintbox{display:block}
/* живая строка автопоиска */
.wlive{position:relative;display:flex;align-items:center;gap:12px;margin:6px 0 4px;padding:10px 14px 12px;border-radius:14px;overflow:hidden;
 background:rgb(255 255 255/.04);border:1px solid rgb(255 255 255/.09);font-size:13px;color:var(--muted)}
.wdot{flex:none;width:10px;height:10px;border-radius:50%;background:rgb(255 255 255/.25)}.wlive.on .wdot{background:var(--good)}
.wtx{flex:1;min-width:0;display:grid}.wtx b{color:var(--fg);font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.wtx span{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:12px}.wcd{flex:none;font:600 12.5px "IBM Plex Mono",monospace;color:var(--fg)}
.wbar{position:absolute;left:0;right:0;bottom:0;height:3px;background:transparent}.wbar i{display:block;height:100%;
 background:linear-gradient(90deg,var(--accent),#e2bd55);transform-origin:left;transform:scaleX(0);transition:transform 1s linear}
/* навигация по разделам */
.snav{position:sticky;top:0;z-index:20;display:flex;gap:6px;overflow-x:auto;scrollbar-width:none;margin:8px -4px 6px;padding:8px 4px;
 background:color-mix(in srgb,var(--bg) 82%,transparent);backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px)}
.snav::-webkit-scrollbar{display:none}
.snav a{flex:none;padding:6px 14px;border-radius:999px;text-decoration:none;color:var(--muted);font-size:13px;border:1px solid transparent;
 transition:color .2s,background .2s,border-color .2s}
.snav a:hover{color:var(--fg)}.snav a.on{color:var(--bg);background:var(--accent);font-weight:600}
main section[id]{scroll-margin-top:64px}
/* плашки поисков по группам, точка «новое за час», вкладки подсказок */
#sv{display:grid;gap:10px}.qgrp{display:flex;flex-wrap:wrap;align-items:center;gap:8px}
.qgl{flex-basis:100%;font-size:11.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}
.fd{flex:none;width:7px;height:7px;border-radius:50%;background:rgb(255 255 255/.18)}.fd.on{background:var(--good);box-shadow:0 0 0 3px color-mix(in srgb,var(--good) 25%,transparent)}
.lg2{font-style:normal;font-size:11px;padding:1px 7px;border-radius:999px;border:1px dashed rgb(226 189 85/.5);color:#e2bd55;white-space:nowrap}
.lg2.ok{border-style:solid;border-color:color-mix(in srgb,var(--accent) 55%,transparent);color:var(--accent)}
.stabs{display:flex;flex-wrap:wrap;gap:6px;margin-top:14px;border-bottom:1px solid rgb(255 255 255/.08);padding-bottom:8px}
.stab{background:none;border:0;border-radius:999px;padding:6px 12px;color:var(--muted);font:inherit;font-size:13px;cursor:pointer}
.stab i{font-style:normal;font-size:11px;margin-left:6px;padding:0 6px;border-radius:999px;background:rgb(255 255 255/.08)}
.stab:hover{color:var(--fg)}.stab.on{color:var(--fg);background:rgb(255 255 255/.08)}.stab.on i{background:var(--accent);color:var(--bg)}
.sg>span{flex-basis:100%}
/* «Обновить» у разделов */
h2 .rf,.h3 .rf{margin-left:10px;vertical-align:middle;border:1px solid rgb(255 255 255/.14);background:none;color:var(--muted);border-radius:999px;
 padding:3px 11px;font-family:inherit;font-size:12px;font-weight:500;line-height:1.4;cursor:pointer;transition:color .2s,border-color .2s}
.rf:hover{color:var(--accent);border-color:var(--accent)}.rf:disabled{cursor:default;opacity:.7}
.rf.busy{color:var(--accent);border-color:color-mix(in srgb,var(--accent) 50%,transparent)}
.rfn{margin-left:10px;font-size:12px;font-weight:400;color:var(--muted)}
/* «Ходовое в каналах» */
.hotg{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,260px),1fr));gap:10px}
.hc2{position:relative;display:grid;grid-template-columns:minmax(0,1fr);gap:6px;align-content:start;min-width:0;padding:11px 12px;border-radius:14px;background:rgb(255 255 255/.05);
 border:1px solid rgb(255 255 255/.10);overflow:hidden;transition:transform .2s,border-color .2s}
.hc2:hover{transform:translateY(-2px);border-color:color-mix(in srgb,var(--accent) 55%,transparent)}
.hh{display:flex;align-items:baseline;gap:8px;min-width:0}.hh>span{flex:none}.hh b{font-size:14px;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.hn{font:700 13px "Unbounded",sans-serif;color:var(--accent)}.hm{font-size:12.5px;color:var(--muted)}.hm b{color:var(--fg)}
.hbar{height:4px;border-radius:4px;background:rgb(255 255 255/.08);overflow:hidden}.hbar i{display:block;height:100%;border-radius:4px;
 background:linear-gradient(90deg,var(--accent),#e2bd55);transform-origin:left;transition:transform .9s cubic-bezier(.2,.8,.2,1)}
.htag{font-size:11px;padding:1px 7px;border-radius:999px;border:1px solid rgb(226 189 85/.45);color:#e2bd55;white-space:nowrap}
.hq{display:flex;flex-wrap:wrap;align-items:center;gap:6px 8px;min-width:0;font-size:12px;color:var(--muted)}.hq code{overflow-wrap:anywhere}.hq code{color:var(--fg);font-size:12px}
.hq a{color:var(--muted)}.hq a:hover{color:var(--accent)}
.hadd{margin-left:auto;border:0;border-radius:999px;padding:5px 12px;background:var(--accent);color:var(--bg);font:inherit;font-size:12.5px;font-weight:600;cursor:pointer}
.hadd:hover{filter:brightness(1.1)}.hadd.done{background:none;border:1px solid color-mix(in srgb,var(--accent) 50%,transparent);color:var(--accent);cursor:default}
/* «Мои продавцы» */
.sgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,300px),1fr));gap:12px}
.slc{position:relative;display:grid;gap:10px;align-content:start;padding:12px;border-radius:16px;background:rgb(255 255 255/.05);
 border:1px solid rgb(255 255 255/.10);box-shadow:inset 0 1px 0 rgb(255 255 255/.10);overflow:hidden;transition:transform .2s,border-color .2s,opacity .25s}
.slc:hover{transform:translateY(-3px);border-color:color-mix(in srgb,var(--accent) 55%,transparent)}.slc.star{border-color:rgb(226 189 85/.5)}
.slc.leaving{opacity:0;transform:scale(.94)}
.slh{display:flex;align-items:center;gap:10px}.av{display:grid;place-items:center;flex:none;width:38px;height:38px;border-radius:12px;
 background:color-mix(in srgb,var(--accent) 22%,transparent);color:var(--accent);font-weight:700;font-size:17px}
.slc.star .av{background:rgb(226 189 85/.18);color:#e2bd55}
.sn{display:grid;min-width:0;flex:1}.sn b{font-size:14.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sn i{font-style:normal;color:var(--muted);font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.pf{color:var(--muted);font-size:12px;text-decoration:none;white-space:nowrap}.pf:hover{color:var(--accent)}
.sth{display:grid;grid-template-columns:repeat(4,1fr);gap:6px}.sth a{position:relative;aspect-ratio:1;border-radius:10px;overflow:hidden;background:var(--soft)}
.sth img{width:100%;height:100%;object-fit:cover;transition:transform .35s}.sth a:hover img{transform:scale(1.08)}
.sth b{position:absolute;left:4px;bottom:4px;font-size:10.5px;padding:2px 5px;border-radius:6px;background:rgb(0 0 0/.65);color:var(--good)}
.sth.none{display:block;color:var(--muted);font-size:12px}
.sst{font-size:12.5px;color:var(--muted)}.sst b{color:var(--fg)}.sst b.pos{color:var(--good)}
.sft{display:flex;flex-wrap:wrap;align-items:center;gap:8px;font-size:12px;color:var(--muted)}.seen{flex:1;min-width:150px}.seen b{color:var(--good)}
.sft button,.go2{border-radius:999px;padding:5px 12px;font:inherit;font-size:12.5px;cursor:pointer;text-decoration:none}
.tg{background:none;border:1px solid rgb(255 255 255/.15);color:var(--muted)}.tg:hover{color:var(--fg)}.tg.on{border-color:#e2bd55;color:#e2bd55}
.rm{background:none;border:0;color:var(--muted)}.rm:hover{color:var(--bad)}
.go{border:0;background:var(--accent);color:var(--bg);font-weight:600}.go:hover{filter:brightness(1.1)}
.go2{border:1px dashed rgb(255 255 255/.25);color:var(--fg)}.go2:hover{border-color:var(--accent);color:var(--accent)}
.h3{font-size:14px;margin:20px 0 10px;display:flex;flex-wrap:wrap;gap:4px 10px;align-items:baseline}.cnt{color:var(--muted);font-weight:500}
.tile{position:relative}.tile .plus{position:absolute;right:12px;top:10px;font-style:normal;color:var(--good);font-weight:700;font-size:13px}
.toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);max-width:calc(100vw - 32px);background:var(--card);color:var(--fg);
 border:1px solid color-mix(in srgb,var(--accent) 50%,transparent);padding:10px 16px;border-radius:12px;box-shadow:0 10px 30px rgb(0 0 0/.35);
 z-index:50;font-size:13.5px;transition:opacity .4s,transform .4s}.toast.out{opacity:0;transform:translate(-50%,10px)}
@media (prefers-reduced-motion:no-preference){section{animation:rise .5s ease-out both}section:nth-child(2){animation-delay:.06s}
 section:nth-child(3){animation-delay:.12s}section:nth-child(4){animation-delay:.18s}@keyframes rise{from{opacity:0;transform:translateY(10px)}}
 /* новые карточки въезжают и светятся; цифры «подпрыгивают» и показывают +N; блик по карточке при наведении */
 html{scroll-behavior:smooth}
 .wlive.on .wdot{animation:wpulse 2s ease-in-out infinite}@keyframes wpulse{50%{box-shadow:0 0 0 6px color-mix(in srgb,var(--good) 0%,transparent),0 0 0 3px color-mix(in srgb,var(--good) 35%,transparent)}}
 .wlive.flash{animation:wflash 1.8s ease-out}@keyframes wflash{0%{border-color:var(--good);box-shadow:0 0 24px color-mix(in srgb,var(--good) 45%,transparent)}}
 .fd.on{animation:wpulse 2.4s ease-in-out infinite}
 .hc2.fresh{animation:pop .7s cubic-bezier(.2,.9,.3,1.25) both,glow 2.4s ease-out}
 .hadd.pop{animation:hpop .5s ease-out}@keyframes hpop{40%{transform:scale(1.12)}}
 .hc.fresh,.slc.fresh{animation:pop .7s cubic-bezier(.2,.9,.3,1.25) both,glow 2.4s ease-out}
 @keyframes pop{from{opacity:0;transform:translateY(14px) scale(.94)}}
 @keyframes glow{0%{box-shadow:0 0 0 2px var(--accent),0 0 28px color-mix(in srgb,var(--accent) 55%,transparent)}}
 .tile.bump{animation:bump .6s ease-out}@keyframes bump{40%{transform:scale(1.04);border-color:var(--good)}}
 .tile .plus{animation:floatup 1.6s ease-out forwards}@keyframes floatup{from{opacity:0;transform:translateY(6px)}20%{opacity:1}to{opacity:0;transform:translateY(-16px)}}
 .pulse{animation:pulse 1.6s ease-in-out infinite}@keyframes pulse{50%{text-shadow:0 0 10px var(--good);opacity:.7}}
 .hc::after,.slc::after{content:"";position:absolute;inset:0;pointer-events:none;border-radius:inherit;
  background:linear-gradient(105deg,transparent 35%,rgb(255 255 255/.09) 50%,transparent 65%);transform:translateX(-120%)}
 .hc:hover::after,.slc:hover::after{transition:transform .8s ease;transform:translateX(120%)}
 .toast{animation:tin .35s ease-out}@keyframes tin{from{opacity:0;transform:translate(-50%,12px)}}}
</style><script>function nav(r){location.href=r==="feed"?"/":"/"+r}</script>"""
    js = """<script>
let S=__S__;const $=id=>document.getElementById(id),esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const save=()=>fetch("/searches",{method:"POST",headers:{"Content-Type":"application/json","X-Goofish-UI":"1"},body:JSON.stringify(S)})
 .then(r=>r.json()).then(j=>{if(j.items){S=j;draw()}else alert(j.error||"ошибка")}).catch(alert);
const fmtn=v=>Math.round(v).toLocaleString("ru-RU");
// толк от запроса: [увидено, прошло в ленту, лучшая прибыль]; 30+ объявлений и меньше 5% в ленте — «мало толку»
function qinfo(q){const s=QST[q],e=(typeof QE==="object"&&QE[q])||0;
 if(!s&&e>=3)return{t:`пусто ${e} раз подряд`,cls:" junk",title:"Площадка раз за разом отвечает «ничего не найдено» — проверьте запрос (опечатка, слишком длинный) или уберите его"};
 if(!s)return{t:"ещё не проверялся",cls:"",title:"Объявлений по этому запросу в базе пока нет"};
 const[n,g,b]=s,junk=n>=30&&g/n<.05;
 return{t:`в ленте ${g} из ${n}`+(b>0?` · +${fmtn(b)} ₽`:""),cls:junk?" junk":"",
  title:junk?"Мало толку: почти всё по этому запросу отсеивается фильтрами — его можно убрать, чтобы автопоиск не тратил на него время":"Сколько объявлений по запросу видели и сколько прошло фильтры в ленту; лучшая прибыль среди них"}}
let JOPEN=false;     // свёрнутый список «мало толку» открыт
const cjk=q=>/[\u4e00-\u9fff]/.test(q);
const kq=s=>(s.site==="mercari"?"jp:":"")+s.q;
// как часто автопоиск открывает поиск: вы закрепили, вы поставили «редко» или решает Щебень (3 дня без вещей 70+ — реже)
function freq(s){return s.rare?["раз в 3 круга"," on","Вы поставили «редко»."]:s.pin?["всегда каждый круг"," pin","Вы закрепили: каждый круг, даже если поиск давно ничего не приносил."]
 :(SUG.slow||[]).includes(kq(s))?["авто: раз в 4 круга"," slow","3 дня без вещей с оценкой 70+ — Щебень сам проверяет реже. Принесёт такую вещь — вернётся в каждый круг."]
 :["авто: каждый круг","","Частоту выбирает Щебень: пока поиск приносит вещи с оценкой 70+, он в каждом круге."]}      // у Mercari свой «толк»: тот же запрос на Goofish — другой поиск
const pl=(n,a)=>a[n%10===1&&n%100!==11?0:n%10>=2&&n%10<=4&&(n%100<10||n%100>=20)?1:2];
// плашка поиска: зелёная точка — принёс новое за последний час; «中/EN» — два варианта запроса, автопоиск выбирает лучший
function chip(s,i){const k=qinfo(kq(s)),F=SUG.fresh||{},m=F[kq(s)]??(s.alt?F[s.alt]:undefined),hot=m!=null&&m<60;
 const tr=s.alt&&!s.picked?((SUG.trials||{})[s.q]||{}):null;
 const lang=!s.alt?"":s.picked?`<i class="lg2 ok" title="${esc(s.why||"")}">${cjk(s.q)?"中":"EN"} ✓</i>`
  :`<i class="lg2" title="Два варианта запроса: «${esc(s.q)}» и «${esc(s.alt)}». Автопоиск чередует их по кругам и оставит тот, что даёт больше вещей в ленту">中/EN ${Math.min(tr[s.q]||0,25)}·${Math.min(tr[s.alt]||0,25)} из 25</i>`;
 return`<span class="qs sv${k.cls}" title="${esc(k.title)}" data-k="${esc((s.site||"")+"|"+s.q)}" data-k2="${esc(s.alt?(s.site||"")+"|"+s.alt:"")}"><span class="fd${hot?" on":""}" title="${hot?`новое ${m} мин назад`:"за последний час новых не было"}"></span>`
 +`<a href="${s.site==="mercari"?"https://jp.mercari.com/search?keyword="+encodeURIComponent(s.q)+"&status=on_sale&sort=created_time&order=desc":"https://www.goofish.com/search?q="+encodeURIComponent(s.q)}" target="_blank">${esc(s.q)}</a>${s.site==="mercari"?'<i class="lg2 jp" title="Mercari, Япония: «до ¥» — в иенах">JP</i>':""}${lang}`
 +(s.max?`<i>до ¥${s.max}</i>`:"")+(s.site==="mercari"?`<button class="mn" data-mn="${i}" title="Mercari показывает только вещи дороже этой цены — дешёвый мусор не занимает страницу выдачи. Нажмите, чтобы изменить">${s.min?"от ¥"+fmtn(s.min):"без нижней цены"}</button>`:"")+`<i class="qy">${esc(k.t)}</i><button class="rr${freq(s)[1]}" data-r="${i}" title="${freq(s)[2]} Нажмите, чтобы сменить: авто → всегда каждый круг → раз в 3 круга">${freq(s)[0]}</button><button class="dp${s.depth>1?" on":""}" data-d="${i}" title="Сколько страниц выдачи листает автопоиск — нажмите, чтобы сменить. Глубже стоит только у запросов, где много вещей проходит в ленту">стр. ${s.depth>1?"1–"+s.depth:"1"}</button><button data-i="${i}" title="Убрать">✕</button></span>`}
const SG=[["pick","Советую","Goofish: самые дорогие в перепродаже модели каталога · ≈ цена продажи в РФ"],
 ["feed","Выгодно в ленте","Goofish: модели, у которых в вашей ленте лучшая прибыль · + лучшая прибыль за вещь"],
 ["line","Линии из ленты","Goofish: бренд + линия или вариация, которая чаще всего встречается у вещей с оценкой 75+"],
 ["quiet","Тихие поиски","Goofish: другие написания бренда, латиница, опечатки — такие объявления почти никто не видит. Шлак не пройдёт: всё идёт через те же фильтры"],
 ["jp","Для Mercari","Mercari (Япония): бренды каталога и «Ходового в каналах» латиницей — так их пишут японские продавцы"],
 ["recent","Недавние","что вы недавно искали — на Goofish и на Mercari"]];
let STAB=null;try{STAB=localStorage.getItem("sheben_stab")}catch(e){}
const BLK=[["","Goofish · Китай"],["mercari","Mercari · Япония"]],
 SUBN={"":"мои","каналы":"из каналов","избранное":"из избранного ★"},
 SUBT={"":"Добавлены вами","каналы":"Добавлены сами из «Ходового в каналах»; обновляются раз в день, выпавшие из ходового через неделю убираются",
  "избранное":"Gemini посмотрел фото ваших ★ и составил по ним запросы. Автопоиск заходит в них раз в 3 круга. Сняли ★ — поиск уберётся; убранный ✕ не вернётся"};
function draw(){const all=S.items.map((s,i)=>[s,i]).filter(([s])=>!s.seller),     // продавцы — в своём разделе ниже
 good=all.filter(([s])=>!qinfo(kq(s)).cls),junk=all.filter(([s])=>qinfo(kq(s)).cls);
 // два блока — две площадки, внутри: мои / из каналов / из избранного
 $("sv").innerHTML=BLK.map(([site,t])=>{const xs=good.filter(([s])=>(s.site||"")===site);if(!xs.length)return"";
   return`<div class="sblk${site?" jp":""}"><div class="sbh"><b>${t}</b><span class="note">${xs.length} ${pl(xs.length,["поиск","поиска","поисков"])}</span><span class="eta" data-eta="${site}"></span></div>`
    +["","каналы","избранное"].map(src=>{const ys=xs.filter(([s])=>(s.src||"")===src);
      return ys.length?`<div class="qgrp"><span class="qgl" title="${esc(SUBT[src])}">${SUBN[src]}</span>${ys.map(([s,i])=>chip(s,i)).join("")}</div>`:""}).join("")+"</div>"}).join("")
  +(good.length||S.items.length?"":'<span class="note">Пока пусто — добавьте запрос ниже или из подсказок.</span>')
  +(junk.length?`<details class="jk"${JOPEN?" open":""}><summary title="Почти всё по этим запросам отсеивается фильтрами или площадка отвечает «ничего не найдено». Автопоиск заходит в них раз в 3 круга. JP — поиск на Mercari">${junk.length} ${pl(junk.length,["поиск почти ничего не даёт","поиска почти ничего не дают","поисков почти ничего не дают"])} (мало толку или пусто) — можно убрать</summary><div class="qrow">${junk.map(([s,i])=>chip(s,i)).join("")}<button class="qs dj" id="s_dropjunk">Убрать все ${junk.length}</button></div></details>`:"");
 const jd=document.querySelector("#sv .jk");if(jd)jd.ontoggle=()=>{JOPEN=jd.open};
 $("s_i").value=S.interval_min;$("s_o").value=S.order||"";
 const have=haveSet();
 // подсказки — вкладками: одна группа на экране, а не пять рядов друг под другом
 const groups=SG.map(([k,t,d])=>[k,t,d,(SUG[k]||[]).filter(x=>!have.has(((x.site||"")+"|"+x.q).toLowerCase()))]).filter(g=>g[3].length);
 if(!groups.some(g=>g[0]===STAB))STAB=groups.length?groups[0][0]:null;
 $("sug").innerHTML=groups.length?`<div class="stabs">${groups.map(([k,t,,xs])=>`<button class="stab${k===STAB?" on":""}" data-tab="${k}">${t}<i>${xs.length}</i></button>`).join("")}</div>`
  +groups.filter(g=>g[0]===STAB).map(([k,t,d,xs])=>`<div class="sg"><span>${d}</span>${xs.map(x=>`<button class="sgc" data-q="${esc(x.q)}" data-site="${x.site||""}" title="${esc(x.why||"")}">`
   +`<em><b class="stg${x.site?" jp":""}">${x.site?"Mercari":"Goofish"}</b>${esc(x.name||"")}</em>${esc(x.q)}${x.hint?`<i>${esc(x.hint)}</i>`:""}</button>`).join("")}</div>`).join(""):"";
 drawHot();etaDraw()}
// уже добавленные поиски: «площадка|запрос» (и второй вариант запроса)
function haveSet(){return new Set(S.items.flatMap(s=>[s.q,s.alt||""].filter(Boolean).map(q=>((s.site||"")+"|"+q).toLowerCase())))}
// когда автопоиск дойдёт до блока площадки; поиск, открытый сейчас, — подсвечен
function etaDraw(){const now=Date.now()/1000+(W.skew||0),Q=W.queue||[],cur=W.on?W.cur:null,ck=cur?(cur[3]||"")+"|"+cur[0]:"";
 document.querySelectorAll("#sv .sv").forEach(e=>e.classList.toggle("now",!!ck&&(e.dataset.k===ck||e.dataset.k2===ck)));
 const hm=t=>new Date(t*1000).toLocaleTimeString("ru-RU",{hour:"2-digit",minute:"2-digit"});
 document.querySelectorAll("[data-eta]").forEach(e=>{const site=e.dataset.eta,o=S.order||"";let t;
  if(!W.on)t="автопоиск выключен — «Следить» справа вверху";
  else if((o==="goofish_only"&&site)||(o==="mercari_only"&&!site))t="пропускается: в круге «"+(site?"только Goofish":"только Mercari")+"»";
  else if(cur&&(cur[3]||"")===site)t=`сейчас: «${cur[0]}»`;
  else{const x=Q.find(q=>(q[1]||"")===site);
   if(x){const s=(W.next_at||0)+x[2]-now;t=s<60?`следующий меньше чем через минуту: «${x[0]}»`:`следующий через ~${Math.round(s/60)} мин: «${x[0]}»`}
   else t=W.cycle_at?"в следующем круге, в "+hm(W.cycle_at):"в следующем круге"}
  if(e.textContent!==t)e.textContent=t})}
let SUG=__SUG__,QST=__QST__,QE=__QE__;
// подсказки и «толк» запросов пересчитываются по ленте — обновляем их, не перезагружая страницу
// подсказки, продавцы, цифры и карточки пересчитываются по ленте — подтягиваем их раз в 20 с, не перезагружая страницу
function refresh(){return fetch("/suggestions").then(r=>r.json()).then(j=>{if(j.sug){SUG=j.sug;QST=j.qst||QST;QE=j.qempty||QE;
  const typing=["s_q","s_m","s_i"].includes(document.activeElement&&document.activeElement.id);
  if(j.searches&&!typing)S=j.searches;     // поиски меняет и сервер: выбор варианта, «из каналов»
  if(!typing)draw();drawSellers()}if(j.live)live(j.live,false)}).catch(()=>{})}
setInterval(()=>{if(!document.hidden)refresh()},20000);
$("sug").onclick=e=>{const t=e.target.closest(".stab");
 if(t){STAB=t.dataset.tab;try{localStorage.setItem("sheben_stab",STAB)}catch(_){}draw();return}
 const b=e.target.closest(".sgc");if(!b)return;$("s_q").value=b.dataset.q;setSite(b.dataset.site||"",true);$("s_q").focus()};
function setSite(v,flash){const el=$("s_site");el.value=v;$("s_add").textContent=v?"Добавить в Mercari":"Добавить в Goofish";
 $("s_q").placeholder=v?"запрос, например kapital ジャケット":"запрос, например stone island 毛衣";
 if(flash){el.classList.remove("flash");void el.offsetWidth;el.classList.add("flash");setTimeout(()=>el.classList.remove("flash"),1600)}}
// --- живые плитки и карточки «Лучшее»/«Новое»
const RM=matchMedia("(prefers-reduced-motion: reduce)").matches;
const im=(u,s)=>{if(!u)return"";if(u.startsWith("//"))u="https:"+u;return /alicdn\\.com/.test(u)&&!/_\\d+x\\d+/.test(u)?u+`_${s}x${s}.jpg`:u};
const tileTxt=(k,v)=>k==="best"?(v>0?"+":"")+fmtn(v)+" ₽":fmtn(v);
let TV={};
function countTo(el,k,from,to){if(RM||from===to){el.textContent=tileTxt(k,to);return}const t0=performance.now(),d=900;
 const step=t=>{const x=Math.min(1,(t-t0)/d),e=1-Math.pow(1-x,3);el.textContent=tileTxt(k,Math.round(from+(to-from)*e));if(x<1)requestAnimationFrame(step)};
 requestAnimationFrame(step);setTimeout(()=>{if(TV[k]===to)el.textContent=tileTxt(k,to)},d+300)}     // окно в фоне: кадры не идут
function tiles(t,first){document.querySelectorAll(".tile[data-k]").forEach(d=>{const k=d.dataset.k,v=+t[k]||0,old=first?0:(TV[k]??v);
  if(first||old!==v){countTo(d.querySelector("b"),k,old,v);
   if(!first&&v>old&&!RM){const f=document.createElement("em");f.className="plus";f.textContent="+"+fmtn(v-old);d.appendChild(f);setTimeout(()=>f.remove(),1700);
    d.classList.remove("bump");void d.offsetWidth;d.classList.add("bump")}}
  TV[k]=v})}
const hcard=r=>`<a class="hc" data-id="${esc(r.id)}" href="${esc(r.url)}" target="_blank"${r.why?` title="${esc(r.why)}"`:""}><span class="hi">${r.img?`<img src="${esc(im(r.img,400))}" referrerpolicy="no-referrer" loading="lazy" alt="">`:""}<i>${esc(r.t)}</i></span>`
 +`<span class="ht">${esc(r.name)}</span>${r.why?`<span class="hw">${esc(r.why)}</span>`:""}<span class="hp">¥${fmtn(r.price)} ${r.p!=null?`<b class="${r.p>0?"pos":"neg"}">${r.p>0?"+":""}${fmtn(r.p)} ₽</b>`:""}</span></a>`;
function grid(id,list,first){const g=$("g_"+id),old=new Set([...g.children].map(a=>a.dataset.id));
 $("s_"+id).hidden=!list.length;g.innerHTML=list.map(hcard).join("");
 if(!first)[...g.children].forEach(a=>{if(!old.has(a.dataset.id))a.classList.add("fresh")})}
function live(L,first){tiles(L.tiles,first);grid("picks",L.picks||[],first);if(L.picks_title)$("h_picks").textContent=L.picks_title;grid("sim",L.sim||[],first);grid("top",L.top,first);grid("new",L.new,first);
 if(L.pulse&&typeof pulseDraw==="function")pulseDraw(L.pulse)}
// --- «Мои продавцы»: за кем следим и кандидаты
let SL_FLASH=new Set();
const ago=ts=>{const m=Math.round((Date.now()-new Date(ts))/60000);return m<1?"только что":m<60?m+" мин назад":Math.floor(m/60)+" ч назад"};
function scard(x,fol){const jp=x.site==="mercari",prof=!x.uid?"":jp?`https://jp.mercari.com/user/profile/${encodeURIComponent(x.uid)}`
  :`https://www.goofish.com/personal?userId=${encodeURIComponent(x.uid)}`,s=x.seen;
 const imgs=x.imgs.length?`<div class="sth">${x.imgs.map(i=>`<a href="${esc(i.url)}" target="_blank" title="прибыль +${fmtn(i.p)} ₽"><img src="${esc(im(i.img,200))}" referrerpolicy="no-referrer" loading="lazy" alt=""><b>+${fmtn(i.p)}</b></a>`).join("")}</div>`
  :fol?'<div class="sth none">Выгодных его вещей в ленте пока нет — появятся после проверки профиля.</div>':"";
 const st=x.n?`в ленте <b>${x.n}</b> · выгодных <b>${x.ok}</b>${x.p?` · лучшая <b class="pos">+${fmtn(x.p)} ₽</b>`:""}`:"в ленте его вещей пока нет";
 const hot=s&&s.added>0&&Date.now()-new Date(s.ts)<3600e3;
 const foot=fol?`<div class="sft"><span class="seen">${s?`проверен ${ago(s.ts)} · ${s.n} вещей${s.added?`, <b class="${hot?"pulse":""}">+${s.added} новых</b>`:", новых нет"}`:"ещё не проверялся — автопоиск откроет профиль в ближайшем круге"}</span>`
   +`<button class="tg${x.every?" on":""}" data-every="${esc(x.uid)}" title="Как часто автопоиск открывает профиль — нажмите, чтобы сменить">${x.every?"каждый круг":"раз в 3 круга"}</button>`
   +`<button class="rm" data-unf="${esc(x.uid)}" title="Перестать следить">✕</button></div>`
  :x.uid?`<div class="sft"><button class="go" data-fol="${esc(x.uid)}">+ Следить</button></div>`
  :`<div class="sft"><a class="go2" href="${esc(x.url)}" target="_blank">Открыть его вещь</a><span class="seen">номер профиля ещё неизвестен: откройте вещь — здесь появится «+ Следить»</span></div>`;
 return`<div class="slc${fol&&x.every?" star":""}${SL_FLASH.has(x.uid)?" fresh":""}" data-uid="${esc(x.uid)}" data-site="${esc(x.site||"")}"><div class="slh"><span class="av">${esc([...(x.nick||"?")][0])}</span>`
  +`<div class="sn"><b>${esc(x.nick)}</b><i>${esc(x.brands||"бренды появятся вместе с вещами")}</i></div>${prof?`<a class="pf" href="${prof}" target="_blank" title="Открыть профиль">${jp?"Mercari · ":""}профиль ↗</a>`:""}</div>`
  +`${imgs}<div class="sst">${st}</div>${foot}</div>`}
function drawSellers(){const f=SUG.followed||[],c=SUG.sellers||[];$("sl_n").textContent=f.length?"· "+f.length:"";
 $("sl").innerHTML=f.map(x=>scard(x,true)).join("")||'<span class="note">Пока никого — нажмите «+ Следить» у кандидата ниже.</span>';
 $("sc").innerHTML=c.map(x=>scard(x,false)).join("")||'<span class="note">Кандидатов пока нет.</span>';SL_FLASH.clear()}
function toast(t){const d=document.createElement("div");d.className="toast";d.textContent=t;document.body.appendChild(d);
 setTimeout(()=>d.classList.add("out"),2800);setTimeout(()=>d.remove(),3300)}
function follow(body,card,msg){if(card)card.classList.add("leaving");
 return fetch("/sellers/follow",{method:"POST",headers:{"Content-Type":"application/json","X-Goofish-UI":"1"},body:JSON.stringify(body)})
  .then(r=>r.json()).then(j=>{if(!j.ok)throw j.error||"ошибка";S=j.searches;toast(msg);if(body.on!==false)SL_FLASH.add(body.uid);return refresh()})
  .catch(e=>{if(card)card.classList.remove("leaving");alert("Не получилось: "+e)})}
$("sellers").onclick=e=>{const b=e.target.closest(".slc button");if(!b)return;const card=b.closest(".slc"),nick=card.querySelector(".sn b").textContent;
 if(b.dataset.fol)follow({uid:b.dataset.fol,nick,on:true,site:card.dataset.site||""},card,`«${nick}» теперь в «Моих продавцах» — автопоиск будет открывать его профиль раз в 3 круга`);
 else if(b.dataset.every){const on=!b.classList.contains("on");
  follow({uid:b.dataset.every,on:true,every:on},null,on?`«${nick}»: профиль каждый круг (~3 мин)`:`«${nick}»: профиль раз в 3 круга (~10 мин)`)}
 else if(b.dataset.unf&&confirm(`Перестать следить за «${nick}»?`))follow({uid:b.dataset.unf,on:false},card,`«${nick}» убран из «Моих продавцов»`)};
// --- «Ходовое в каналах»: что продают Telegram-каналы, «+ В автопоиск» — запрос в «Мои поиски»
let HOT_SEEN=null;
const jpq=x=>{const l=x.lat||"";return /[a-z]/.test(l)&&!cjk(l)?l:""};     // запрос для Mercari — латиница
function drawHot(){const xs=SUG.hot||[],have=haveSet(),mx=Math.max(1,...xs.map(x=>x.n)),first=!HOT_SEEN,old=HOT_SEEN;
 HOT_SEEN=new Set(xs.map(x=>x.brand+"|"+x.model));$("hot").hidden=!xs.length;
 $("hotg").innerHTML=xs.map(x=>{const w=(x.n/mx).toFixed(3),jq=jpq(x);
  const inq2=[x.q,x.cn,x.lat].some(q=>q&&have.has("|"+q.toLowerCase())),inj=jq&&have.has("mercari|"+jq.toLowerCase());
  return`<div class="hc2${old&&!old.has(x.brand+"|"+x.model)?" fresh":""}"><div class="hh"><b>${esc(x.brand)}${x.model?" · "+esc(x.model):""}</b>`
   +(x.in_cat?"":'<span class="htag" title="Бренда нет в каталоге: Щебень оценит такие вещи грубее, без модели и точной цены продажи">вне каталога</span>')
   +`<span class="hn" title="Постов за 4 месяца">${x.n}</span></div><div class="hbar"><i data-w="${w}" style="transform:scaleX(${first&&!RM?0:w})"></i></div>`
   +`<div class="hm">${x.med?`просят <b>${fmtn(x.med)} ₽</b>${x.lo!==x.hi?` (${fmtn(x.lo)}–${fmtn(x.hi)})`:""}`:"цены нет"}${x.sold?` · продано <b>${x.sold}</b>`:""} · ${esc(x.ch.join(", "))}</div>`
   +`<div class="hq">Goofish <code>${esc(x.cn||x.q)}</code>${x.cn?` <span title="Автопоиск сравнит оба варианта и оставит лучший">или <code>${esc(x.lat||x.q)}</code></span>`:""}`
   +`${jq?` · Mercari <code>${esc(jq)}</code>`:""}${x.url?`<a href="${esc(x.url)}" target="_blank" title="Пример поста в канале">пост ↗</a>`:""}</div>`
   +`<div class="hq"><button class="hadd${inq2?" done":""}" data-q="${esc(x.q)}" data-site=""${inq2?" disabled":""}>${inq2?"✓ Goofish":"+ Goofish"}</button>`
   +(jq?`<button class="hadd jp${inj?" done":""}" data-q="${esc(x.q)}" data-site="mercari"${inj?" disabled":""}>${inj?"✓ Mercari":"+ Mercari"}</button>`:"")+`</div></div>`}).join("");
 // полоски «сколько постов» вырастают при открытии (таймер, а не кадр: в фоновом окне кадры не идут)
 if(first&&!RM)setTimeout(()=>document.querySelectorAll("#hotg .hbar i").forEach(i=>i.style.transform=`scaleX(${i.dataset.w})`),80)}
$("hotg").onclick=e=>{const b=e.target.closest("button.hadd");if(!b||b.disabled)return;
 const x=(SUG.hot||[]).find(h=>h.q===b.dataset.q)||{q:b.dataset.q};b.disabled=true;b.classList.add("pop");
 if(b.dataset.site==="mercari"){const q=jpq(x);if(!q)return;
  S.items=S.items.filter(s=>!(s.site==="mercari"&&s.q.toLowerCase()===q.toLowerCase()));S.items.push({q,max:null,site:"mercari"});save();
  toast(`«${q}» добавлен в Mercari — блок «Mercari · Япония», проверится в ближайшем круге`);return}
 const q=x.cn||x.lat||x.q,alt=x.cn?(x.lat||x.q):"";
 S.items=S.items.filter(s=>s.site==="mercari"||s.q.toLowerCase()!==q.toLowerCase());S.items.push({q,alt,max:null});save();
 toast(alt?`«${q}» / «${alt}» в Goofish: проверю оба варианта и оставлю лучший`:`«${q}» добавлен в Goofish — проверится в ближайшем круге`)};
// --- живая строка автопоиска: состояние раз в 3 с, обратный отсчёт и полоска — каждую секунду
let W={},WLOG="";
function wDraw(){const now=Date.now()/1000+(W.skew||0),el=$("wl");if(!el)return;el.classList.toggle("on",!!W.on);
 const c=W.cur;$("wl_s").textContent=W.on?`Автопоиск · круг ${W.cycle||0}`+(c?` · ${c[3]==="mercari"?"Mercari ":""}«${c[0]}»${c[2]>1?` стр. ${c[1]} из ${c[2]}`:""} · поиск ${W.done} из ${W.total}`:"")
  +(W.quiet?" · ночь в Китае":""):(W.stopped||"Автопоиск выключен — «Следить» справа вверху");
 const left=(W.next_at||0)-now,cyc=(W.cycle_at||0)-now;
 $("wl_c").textContent=!W.on?"":left>0.5?`шаг через ${Math.ceil(left)} с`:cyc>0?"круг в "+new Date(W.cycle_at*1000).toLocaleTimeString("ru-RU",{hour:"2-digit",minute:"2-digit"}):"";
 $("wl_b").style.transform=`scaleX(${W.on&&W.gap&&left>0?Math.max(0,Math.min(1,1-left/W.gap)):W.on?1:0})`;etaDraw();ringTick()}
function wPoll(){if(document.hidden)return;fetch("/watch").then(r=>r.json()).then(j=>{W=j;W.skew=(j.now||Date.now()/1000)-Date.now()/1000;
  const last=(j.log||[])[0]||"";$("wl_l").textContent=last?"последнее: "+last:"";
  if(last&&last!==WLOG){const hit=/новых [1-9]/.test(last);if(WLOG&&hit&&!RM){const el=$("wl");el.classList.remove("flash");void el.offsetWidth;el.classList.add("flash")}WLOG=last}
  wDraw();ringDraw()}).catch(()=>{})}
wPoll();setInterval(wPoll,3000);setInterval(wDraw,1000);
// --- навигация по разделам: подсвечиваем тот, что сейчас на экране
(()=>{const links=[...document.querySelectorAll("#snav a")],by=Object.fromEntries(links.map(a=>[a.getAttribute("href").slice(1),a]));
 if(!("IntersectionObserver" in window))return;const vis=new Map();
 const io=new IntersectionObserver(es=>{es.forEach(e=>vis.set(e.target.id,e.isIntersecting?e.intersectionRatio:0));
  let best=null,bv=0;for(const[id,v]of vis)if(v>bv&&!document.getElementById(id).hidden){best=id;bv=v}
  links.forEach(a=>a.classList.toggle("on",a===by[best]))},{threshold:[0,.15,.4,.7],rootMargin:"-60px 0px -35% 0px"});
 Object.keys(by).forEach(id=>{const el=document.getElementById(id);if(el)io.observe(el)})})();
// --- «Обновить» у разделов: каналы — собрать сейчас; поиски — пересобрать «из каналов» и новый круг; кандидаты — пересчитать
function rfBusy(id,on,txt){const b=$(id);b.disabled=on;b.classList.toggle("busy",on);b.textContent=on?"↻ Обновляю…":"↻ Обновить";
 if(txt!=null)$(id+"_n").textContent=txt}
const UIH={"Content-Type":"application/json","X-Goofish-UI":"1"};
$("rf_h").onclick=()=>{rfBusy("rf_h",true,"собираю посты каналов…");
 fetch("/market/config",{method:"POST",headers:UIH,body:JSON.stringify({run:true})}).then(r=>r.json()).then(()=>{let seen=false,n=0;
  const t=setInterval(()=>{fetch("/market/config").then(r=>r.json()).then(c=>{n++;seen=seen||c.running;
   if((seen&&!c.running)||n>200){clearInterval(t);rfBusy("rf_h",false,c.last?"собрано в "+c.last.split(" ").pop():"");refresh();toast("Посты каналов собраны — «Ходовое» обновлено")}
   else if(c.running)$("rf_h_n").textContent="собираю посты каналов… это пара минут"}).catch(()=>{})},3000)})
 .catch(e=>{rfBusy("rf_h",false,"");alert("Не получилось: "+e)})};
$("rf_s").onclick=()=>{rfBusy("rf_s",true,"");fetch("/searches/refresh",{method:"POST",headers:UIH,body:"{}"}).then(r=>r.json())
 .then(()=>setTimeout(()=>refresh().then(()=>{rfBusy("rf_s",false,"обновлено в "+new Date().toLocaleTimeString("ru-RU",{hour:"2-digit",minute:"2-digit"}));
  toast("Поиски обновлены; если автопоиск ждал следующего круга — начинает сейчас")}),2500))
 .catch(e=>{rfBusy("rf_s",false,"");alert("Не получилось: "+e)})};
$("rf_c").onclick=()=>{rfBusy("rf_c",true);refresh().then(()=>{rfBusy("rf_c",false);
 document.querySelectorAll("#sc .slc").forEach((c,i)=>{c.classList.remove("fresh");void c.offsetWidth;c.style.animationDelay=(i*40)+"ms";c.classList.add("fresh")})})};
// --- «Сейчас»: кольцо круга автопоиска (данные /watch: plan — весь круг, done — сколько открыто, yields — новых за сеанс)
const JPC="#e0526b",SVGN="http://www.w3.org/2000/svg";
const rpt=(r,a)=>{const t=(a-90)*Math.PI/180;return[(r*Math.cos(t)).toFixed(2),(r*Math.sin(t)).toFixed(2)]};
const rarc=(r0,r1,a0,a1)=>{const L=a1-a0>180?1:0,[x0,y0]=rpt(r1,a0),[x1,y1]=rpt(r1,a1),[x2,y2]=rpt(r0,a1),[x3,y3]=rpt(r0,a0);
 return`M${x0} ${y0}A${r1} ${r1} 0 ${L} 1 ${x1} ${y1}L${x2} ${y2}A${r0} ${r0} 0 ${L} 0 ${x3} ${y3}Z`};
let RANG=null;
function ringBuild(P){const svg=$("ring"),n=P.length,st=360/n,gap=Math.min(1.2,st*.25);
 svg.innerHTML=`<circle r="105" class="rbg"/>`+P.map(([q,s],i)=>{const a0=i*st+gap/2,a1=(i+1)*st-gap/2,c=s==="mercari"?"jp":"gf";
   return`<path class="rs ${c} next" data-i="${i}" d="${rarc(98,112,a0,a1)}"/><rect class="ry ${c}" data-i="${i}" x="${-Math.min(2,st*.9)}" width="${Math.min(4,st*1.8)}" style="y:-116px;height:0px" transform="rotate(${(i+.5)*st})"/>`}).join("")
  +`<g id="rptr" style="transform:rotate(0deg)"><circle cx="0" cy="-88" r="4"/></g><text class="rc1" id="rc1" y="-14"></text><text class="rc2" id="rc2" y="8"></text>`
  +`<text class="rc3" id="rc3" y="30"></text><text class="rc4" id="rc4" y="50"></text>`;RANG=null}
function ringDraw(){const svg=$("ring");if(!svg)return;const P=W.plan||[],n=P.length;
 if(!n){if(svg.dataset.sig!=="0"){svg.dataset.sig="0";svg.innerHTML=`<circle r="105" class="rbg"/><text class="rc1" y="-2">${W.on?"…":"выкл."}</text>`
   +`<text class="rc2" y="22">${W.on?"круг начинается":"«Следить» справа вверху"}</text>`}return}
 const sig=JSON.stringify(P);if(svg.dataset.sig!==sig){svg.dataset.sig=sig;ringBuild(P)}
 const cur=W.on?Math.max(0,(W.done||1)-1):-1,Y=W.yields||{},st=360/n,mx=Math.max(1,...Object.values(Y));
 svg.querySelectorAll(".rs").forEach(p=>{const i=+p.dataset.i;p.classList.toggle("done",i<cur);p.classList.toggle("now",i===cur);p.classList.toggle("next",i>cur)});
 svg.querySelectorAll(".ry").forEach(r=>{const[q,s]=P[+r.dataset.i],y=Y[(s||"")+"|"+q]||0,l=y?6+30*Math.log2(1+y)/Math.log2(1+mx):0;
  r.style.height=l+"px";r.style.y=-(116+l)+"px"});
 if(cur>=0){let a=(cur+.5)*st;if(RANG!=null){while(a<RANG-180)a+=360;while(a>RANG+180)a-=360}RANG=a;$("rptr").style.transform=`rotate(${a}deg)`}
 ringTick()}
function ringTick(){if(!$("rc1"))return;const now=Date.now()/1000+(W.skew||0),P=W.plan||[],left=(W.next_at||0)-now,cyc=(W.cycle_at||0)-now;
 $("rc1").textContent=W.on?`круг ${W.cycle||0}`:"выкл.";
 $("rc2").textContent=W.on?`поиск ${W.done||0} из ${P.length}`:"«Следить» справа вверху";
 $("rc3").textContent=!W.on?"":left>0.5?`шаг через ${Math.ceil(left)} с`:cyc>0?"следующий круг в "+new Date(W.cycle_at*1000).toLocaleTimeString("ru-RU",{hour:"2-digit",minute:"2-digit"}):"";
 const c=W.cur,jp=(W.queue||[]).find(q=>q[1]==="mercari");
 $("rc4").textContent=!W.on?"":c&&c[3]==="mercari"?"сейчас Mercari":jp?`Mercari через ~${Math.max(1,Math.round(((W.next_at||0)+jp[2]-now)/60))} мин`:""}
$("ring").addEventListener("mousemove",e=>{const p=e.target.closest(".rs,.ry"),tip=$("rtip");if(!p){tip.hidden=true;return}
 const i=+p.dataset.i,[q,s]=(W.plan||[])[i]||[],cur=Math.max(0,(W.done||1)-1),y=(W.yields||{})[(s||"")+"|"+q]||0,
  qx=(W.queue||[]).find(x=>x[0]===q&&(x[1]||"")===(s||"")),now=Date.now()/1000+(W.skew||0);
 const when=!W.on?"":i<cur?"пройден в этом круге":i===cur?"открыт сейчас":qx?`через ~${Math.max(1,Math.round(((W.next_at||0)+qx[2]-now)/60))} мин`:"";
 tip.innerHTML=`<b>${esc(q)}</b><i>${s==="mercari"?"Mercari · Япония":"Goofish · Китай"}</i><br>новых за сеанс: <span class="pos">${y}</span>${when?"<br>"+when:""}`;
 const b=$("ring").parentNode.getBoundingClientRect();tip.style.left=Math.min(e.clientX-b.left+12,b.width-160)+"px";tip.style.top=(e.clientY-b.top+12)+"px";tip.hidden=false});
$("ring").addEventListener("mouseleave",()=>{$("rtip").hidden=true});
// --- «Пульс»: новые вещи по 5 минут за 3 часа, Goofish и Mercari; точки — находки уровня A
let PL=null,PSEEN=null,PW=0;
function smooth(pts){if(!pts.length)return"";let d=`M${pts[0][0].toFixed(1)} ${pts[0][1].toFixed(1)}`;
 for(let i=0;i<pts.length-1;i++){const p0=pts[i-1]||pts[i],p1=pts[i],p2=pts[i+1],p3=pts[i+2]||p2;
  d+=`C${(p1[0]+(p2[0]-p0[0])/6).toFixed(1)} ${(p1[1]+(p2[1]-p0[1])/6).toFixed(1)} ${(p2[0]-(p3[0]-p1[0])/6).toFixed(1)} ${(p2[1]-(p3[1]-p1[1])/6).toFixed(1)} ${p2[0].toFixed(1)} ${p2[1].toFixed(1)}`}
 return d}
function pulseDraw(P){if(!P)return;const svg=$("pulse"),w=Math.max(260,$("pchart").clientWidth||600),h=200,L=30,R=12,T=12,B=24,span=3*3600;
 const now=Date.now()/1000,x0=now-span;
 // данные считаются при пересчёте ленты; пока новых вещей нет, они стареют — сдвигаем их под текущее окно, иначе линия уезжает за рамку
 const sh=Math.floor((x0-P.t0)/P.step);
 if(sh>0){const cut=a=>a.slice(sh).concat(Array(Math.min(sh,a.length)).fill(0));
  P={...P,t0:P.t0+sh*P.step,gf:cut(P.gf),jp:cut(P.jp),finds:P.finds.filter(f=>f.t>=x0)}}
 PL=P;const X=t=>L+(t-x0)/span*(w-L-R),mx=Math.max(4,...P.gf,...P.jp),Yv=v=>h-B-v/mx*(h-T-B);
 const pts=(a,z)=>a.map((v,i)=>[X(P.t0+(i+.5)*P.step),Yv(z?0:v)]),line=(a,z)=>smooth(pts(a,z)),
  area=(a,z)=>{const p=pts(a,z);return smooth(p)+`L${p[p.length-1][0].toFixed(1)} ${h-B}L${p[0][0].toFixed(1)} ${h-B}Z`};
 const fresh=PW!==w||!svg.querySelector(".pa");
 if(fresh){PW=w;svg.setAttribute("viewBox",`0 0 ${w} ${h}`);
  svg.innerHTML=`<defs><linearGradient id="g_gf" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="var(--accent)" stop-opacity=".45"/><stop offset="1" stop-color="var(--accent)" stop-opacity="0"/></linearGradient>`
   +`<linearGradient id="g_jp" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${JPC}" stop-opacity=".45"/><stop offset="1" stop-color="${JPC}" stop-opacity="0"/></linearGradient></defs>`
   +`<g id="p_ax"></g><path class="pa gf" id="pa_gf"/><path class="pa jp" id="pa_jp"/><path class="pl gf" id="pl_gf"/><path class="pl jp" id="pl_jp"/><g id="p_now"></g><g id="p_f"></g>`;
  for(const[k,a]of[["gf",P.gf],["jp",P.jp]]){$("pa_"+k).style.d=`path("${area(a,true)}")`;$("pl_"+k).style.d=`path("${line(a,true)}")`}}
 // оси: 3 горизонтали и метки времени каждые 30 минут
 let ax="";for(const v of[mx/2,mx])ax+=`<line class="pg" x1="${L}" x2="${w-R}" y1="${Yv(v)}" y2="${Yv(v)}"/><text class="pt" x="${L-6}" y="${Yv(v)+3}" text-anchor="end">${Math.round(v)}</text>`;
 ax+=`<line class="pg" x1="${L}" x2="${w-R}" y1="${h-B}" y2="${h-B}"/>`;
 for(let t=Math.ceil(x0/1800)*1800;t<now;t+=1800)ax+=`<text class="pt" x="${X(t)}" y="${h-6}" text-anchor="middle">${new Date(t*1000).toLocaleTimeString("ru-RU",{hour:"2-digit",minute:"2-digit"})}</text>`;
 $("p_ax").innerHTML=ax;$("p_now").innerHTML=`<line class="pnow" x1="${X(now)}" x2="${X(now)}" y1="${T}" y2="${h-B}"/><circle class="pdot" cx="${X(now)}" cy="${h-B}" r="4"/>`;
 setTimeout(()=>{for(const[k,a]of[["gf",P.gf],["jp",P.jp]]){$("pa_"+k).style.d=`path("${area(a)}")`;$("pl_"+k).style.d=`path("${line(a)}")`}},40);
 // находки A: на линии своей площадки; новые «выпрыгивают»
 const old=PSEEN;PSEEN=new Set(P.finds.map(f=>f.id));
 $("p_f").innerHTML=P.finds.map((f,i)=>{const a=f.site?P.jp:P.gf,j=Math.max(0,Math.min(a.length-1,Math.floor((f.t-P.t0)/P.step))),
   r=4+Math.min(4,(f.p||0)/6000);return`<circle class="pf${f.site?" jp":""}${old&&!old.has(f.id)?" fresh":""}" data-i="${i}" cx="${X(f.t).toFixed(1)}" cy="${(Yv(a[j])-2).toFixed(1)}" r="${r.toFixed(1)}"/>`}).join("");
 const sum=a=>a.reduce((x,y)=>x+y,0);
 $("p_sum").textContent=`за 3 часа: Goofish ${sum(P.gf)} · Mercari ${sum(P.jp)} · 75+ ${P.finds.length}`}
$("pulse").addEventListener("mousemove",e=>{const c=e.target.closest(".pf"),tip=$("ptip");if(!c||!PL){tip.hidden=true;return}
 const f=PL.finds[+c.dataset.i];if(!f){tip.hidden=true;return}
 tip.innerHTML=(f.img?`<img src="${esc(im(f.img,200))}" referrerpolicy="no-referrer" alt="">`:"")+`<b>${esc(f.name)}</b><i>${esc(f.price)} · ${f.site?"Mercari":"Goofish"} · `
  +new Date(f.t*1000).toLocaleTimeString("ru-RU",{hour:"2-digit",minute:"2-digit"})+`</i>${f.p!=null?`<br><span class="pos">${f.p>0?"+":""}${fmtn(f.p)} ₽</span>`:""}`;
 const b=$("pchart").getBoundingClientRect();tip.style.left=Math.max(0,Math.min(e.clientX-b.left+14,b.width-240))+"px";tip.style.top=Math.max(0,e.clientY-b.top-40)+"px";tip.hidden=false});
$("pulse").addEventListener("mouseleave",()=>{$("ptip").hidden=true});
$("pulse").addEventListener("click",e=>{const c=e.target.closest(".pf");if(c&&PL){const f=PL.finds[+c.dataset.i];if(f&&f.url)window.open(f.url,"_blank")}});
addEventListener("resize",()=>{clearTimeout(window._prs);window._prs=setTimeout(()=>pulseDraw(PL),200)});
setInterval(()=>{if(!document.hidden)pulseDraw(PL)},60000);      // «сейчас» уезжает вправо, старое уходит влево
// --- «Аналитика»: грузится, когда раздел показался на экране; полосы и клетки вырастают
const pct=(a,b)=>!b?"":a/b>=.1?Math.round(100*a/b)+"%":a/b>=.001?(100*a/b).toFixed(1)+"%":"<0.1%";
function funnelDraw(F){const mx=Math.max(1,...F.map(x=>Math.max(x.gf,x.jp))),w=v=>v?Math.max(1.5,100*Math.log10(1+v)/Math.log10(1+mx)):0;
 $("funnel").innerHTML=`<div class="fr fh"><span></span><span>Goofish</span><span>Mercari</span><span style="text-align:right">дошло</span></div>`
  +F.map((x,i)=>{const p=F[i-1];return`<div class="fr" title="${esc(x.why)}"><span class="ft">${esc(x.t)}</span>`
   +`<div class="fb"><i class="gf" style="--w:${w(x.gf)}%;transition-delay:${i*90}ms"></i><b>${fmtn(x.gf)}</b></div>`
   +`<div class="fb"><i class="jp" style="--w:${w(x.jp)}%;transition-delay:${i*90+45}ms"></i><b>${fmtn(x.jp)}</b></div>`
   +`<span class="fc">${p?pct(x.gf+x.jp,p.gf+p.jp):""}</span></div>`}).join("");
 setTimeout(()=>$("funnel").classList.add("on"),40)}
function heatDraw(S){const hn=S.hour,hdr=`<span></span>`+Array.from({length:24},(_,h)=>`<span class="hx">${h%3?"":String(h).padStart(2,"0")}</span>`).join("")+`<span class="hx">всего</span>`;
 $("h_note").textContent=`по вашему времени (UTC${S.tz>=0?"+":""}${S.tz}) · Пекин ${8-S.tz>=0?"+":""}${8-S.tz} ч, Токио ${9-S.tz>=0?"+":""}${9-S.tz} ч`;
 $("heat").innerHTML=hdr+S.heat.map((r,ri)=>{const m=Math.max(1,...r.h),c=r.site==="mercari"?"jp":r.site==="brand"?"br":"gf";
  return`<span class="hl ${c==="br"?"":c}" title="${esc(r.name)}">${esc(r.name)}</span>`+r.h.map((v,h)=>`<span class="hc3 ${c}${h===hn?" cur":""}" style="--o:${(.07+.93*v/m).toFixed(2)};transition-delay:${(ri*24+h)*6}ms" title="${esc(r.name)}, ${String(h).padStart(2,"0")}:00–${String((h+1)%24).padStart(2,"0")}:00 — ${v} объявл."></span>`).join("")
   +`<span class="hn">${fmtn(r.n)}</span>`}).join("");
 setTimeout(()=>$("heat").classList.add("on"),40)}
let STATS_AT=0;
function statsLoad(){if(Date.now()-STATS_AT<300000)return;STATS_AT=Date.now();
 fetch("/stats").then(r=>r.json()).then(S=>{if(S.pending){STATS_AT=0;setTimeout(statsLoad,5000);return}funnelDraw(S.funnel);heatDraw(S)}).catch(()=>{STATS_AT=0})}
if("IntersectionObserver" in window){new IntersectionObserver(es=>{if(es.some(e=>e.isIntersecting))statsLoad()},{rootMargin:"200px"}).observe($("stats"))}else statsLoad();
setInterval(()=>{if(!document.hidden&&$("stats").getBoundingClientRect().top<innerHeight)statsLoad()},60000);
live(__LIVE__,true);drawSellers();
function add(q,mx,site){q=q.trim();if(!q)return;site=site||"";
 S.items=S.items.filter(s=>!(s.q.toLowerCase()===q.toLowerCase()&&(s.site||"")===site));S.items.push({q,max:+mx||null,...(site?{site}:{})});save()}
$("sv").onclick=e=>{if(e.target.id==="s_dropjunk"){const n=S.items.filter(s=>!s.seller&&qinfo(kq(s)).cls).length;
  if(confirm(`Убрать ${n} запросов с пометкой «мало толку» или «пусто» из «Моих поисков»?`)){S.items=S.items.filter(s=>s.seller||!qinfo(kq(s)).cls);JOPEN=false;save()}return}
 const d=e.target.closest("button[data-d]");if(d){const s=S.items[+d.dataset.d];s.depth=(s.depth||1)%3+1;save();return}
 const mn=e.target.closest("button[data-mn]");if(mn){const s=S.items[+mn.dataset.mn],v=prompt(`«${s.q}» на Mercari: показывать вещи от, ¥ (0 — без нижней цены)`,s.min??8000);
  if(v!==null&&v.trim()!==""&&!isNaN(+v)){s.min=Math.max(0,Math.round(+v));save();toast(s.min?`«${s.q}»: от ¥${fmtn(s.min)} — со следующего захода`:`«${s.q}»: без нижней цены`)}return}
 const rr=e.target.closest("button[data-r]");if(rr){const s=S.items[+rr.dataset.r];
  if(s.rare){delete s.rare;toast(`«${s.q}»: частоту снова выбирает Щебень`)}else if(s.pin){delete s.pin;s.rare=true;toast(`«${s.q}»: раз в 3 круга`)}
  else{s.pin=true;toast(`«${s.q}»: всегда каждый круг`)}save();return}
 const b=e.target.closest("button[data-i]");if(b){S.items.splice(+b.dataset.i,1);save()}};
$("s_add").onclick=()=>{add($("s_q").value,$("s_m").value,$("s_site").value);$("s_q").value=$("s_m").value=""};
$("s_site").onchange=()=>setSite($("s_site").value,false);
$("s_q").onkeydown=e=>{if(e.key==="Enter")$("s_add").click()};
$("s_i").onchange=()=>{S.interval_min=+$("s_i").value||15;save()};
$("s_o").onchange=()=>{S.order=$("s_o").value||undefined;save();toast("Порядок площадок — со следующего круга автопоиска")};
setSite($("s_site").value,false);
draw();
// заказы клиентов
let O={items:[],matches:{},brands:{}};
const thumb=u=>{if(!u)return"";if(u.startsWith("//"))u="https:"+u;return /alicdn\\.com/.test(u)&&!/_\\d+x\\d+/.test(u)?u+"_200x200.jpg":u};
function oSave(){return fetch("/orders",{method:"POST",headers:{"Content-Type":"application/json","X-Goofish-UI":"1"},body:JSON.stringify({items:O.items})})
 .then(r=>r.json()).then(j=>{if(!j.items)throw j.error||"ошибка";O.items=j.items;O.matches=j.matches||{};oDraw()}).catch(e=>alert("Заказ не сохранён: "+e))}
function oDraw(){const act=O.items.filter(o=>o.active);
 $("ord").innerHTML=act.map(o=>{const ms=O.matches[o.id]||[],yes=ms.filter(m=>m.fit==="да"),maybe=ms.length-yes.length;
  const what=[(o.brand||"").split("|").map(s=>s.trim()).filter(Boolean).join(", "),o.model,o.words].filter(Boolean).join(" · ")||"—";
  return`<div class="oc"><div class="ohd"><b>${esc(o.client||"клиент")}</b><span>${esc(what)}${o.size?" · размер "+esc(o.size):""}${o.gender?" · "+(o.gender==="м"?"мужское":"женское"):""}${o.due?" · нужно к "+esc(o.due.split("-").reverse().join(".")):""} · платит <b>${o.budget_min?"от "+fmtn(o.budget_min)+" ":""}до ${fmtn(o.budget)} ₽</b></span>${o.note?`<i>${esc(o.note)}</i>`:""}</div>
   <div class="oms">${ms.slice(0,4).map(m=>`<a class="om" href="${esc(m.url)}" target="_blank" title="${esc(m.title)}">${m.image?`<img src="${esc(thumb(m.image))}" referrerpolicy="no-referrer" loading="lazy" alt="">`:""}<span>¥${fmtn(m.price)}${m.cost?" · затраты "+fmtn(m.cost)+" ₽":""}${m.fit==="да"?"":" · размер?"}${m.late?' · <u class="late">не успеет</u>':""}</span><b title="Запас до бюджета клиента: сколько останется вам, если он заплатит верхнюю границу">запас +${fmtn(m.profit)} ₽</b></a>`).join("")||'<span class="note">Пока ничего подходящего — уведомлю, когда появится.</span>'}</div>
   <div class="oft"><span class="note">подходит ${yes.length}${maybe?` · ещё ${maybe} без размера`:""}</span>${ms.length?`<a href="/#order=${o.id}">все в ленте →</a>`:""}<button data-close="${o.id}" title="Клиент купил или передумал">закрыть</button></div></div>`}).join("")
  ||'<span class="note">Заказов нет — добавьте ниже.</span>'}
let OB=[];
function oBrands(){$("o_bl").innerHTML=OB.map((b,i)=>`<button class="obc" data-ob="${i}" title="Убрать">${esc(b)} ×</button>`).join("")}
function oModels(){const ms=OB.length===1?O.brands[OB[0]]||[]:[];$("o_m").innerHTML='<option value="">любая модель</option>'+ms.map(m=>`<option>${esc(m)}</option>`).join("");$("o_m").disabled=!ms.length}
fetch("/orders").then(r=>r.json()).then(j=>{O=j;$("o_b").innerHTML='<option value="">бренд…</option>'+Object.keys(j.brands).map(b=>`<option>${esc(b)}</option>`).join("");oModels();oDraw();oLeads()});
$("o_b").onchange=()=>{const b=$("o_b").value;if(b&&!OB.includes(b))OB.push(b);$("o_b").value="";oBrands();oModels()};
$("o_bl").onclick=e=>{const b=e.target.closest("[data-ob]");if(b){OB.splice(+b.dataset.ob,1);oBrands();oModels()}};
$("o_add").onclick=()=>{const o={client:$("o_c").value,brand:OB.join(" | "),model:$("o_m").value,words:$("o_w").value,size:$("o_s").value,
  budget:+$("o_p").value,budget_min:+$("o_p0").value||0,gender:$("o_g").value,due:$("o_d").value,note:$("o_n").value,active:true};
 if(!o.budget||!(o.brand||o.words.trim())){alert("Нужны бренд (или слова) и сколько платит клиент");return}
 O.items.push(o);oSave().then(()=>{["o_c","o_w","o_s","o_p","o_p0","o_n","o_g","o_d","o_lead"].forEach(id=>$(id).value="");OB=[];oBrands();oModels()})};
// лид -> заготовка заказа: поля формы заполняются, сохраняете вы кнопкой «Добавить заказ»
function oFill(l){OB=(l.brand||"").split("|").map(s=>s.trim()).filter(b=>b in O.brands);oBrands();oModels();
 $("o_c").value=l.client||"";$("o_w").value=l.words||"";$("o_s").value=l.size||"";$("o_p").value=l.budget||"";$("o_p0").value=l.budget_min||"";
 $("o_g").value=l.gender||"";$("o_n").value=l.note||"";$("o_c").scrollIntoView({block:"center",behavior:"smooth"})}
function oLeads(){const L=O.leads||[];$("o_leads").innerHTML=L.length?'<div class="note">Ищут в каналах и чатах:</div>'+L.map((x,i)=>
 `<div class="old"><span><b>${esc(x.src)}</b> · ${esc(x.ts.split("-").reverse().join("."))} · ${esc(x.text)}</span>${x.url?`<a href="${esc(x.url)}" target="_blank">пост</a>`:""}<button data-lead="${i}">в заказ</button></div>`).join(""):""}
$("o_leads").onclick=e=>{const b=e.target.closest("[data-lead]");if(b)oFill(O.leads[+b.dataset.lead].lead)};
$("o_parse").onclick=()=>{const t=$("o_lead").value.trim();if(!t)return;
 fetch("/lead",{method:"POST",headers:{"Content-Type":"application/json","X-Goofish-UI":"1"},body:JSON.stringify({text:t})}).then(r=>r.json())
  .then(j=>{if(!j.lead)throw 0;oFill(j.lead);if(!j.lead.brand)alert("Бренд в тексте не нашёлся — выберите его сами")}).catch(()=>alert("Не получилось разобрать текст"))};
$("ord").onclick=e=>{const b=e.target.closest("[data-close]");if(!b)return;const o=O.items.find(x=>x.id===b.dataset.close);
 if(o&&confirm(`Закрыть заказ «${o.client||"клиент"}»?`)){o.active=false;oSave()}};
// длинные пояснения разделов и графиков — под «?» у заголовка: наведите или нажмите
document.querySelectorAll("main section > p.note, .vbox > p.vnote").forEach(p=>{const h=p.parentNode.querySelector(":scope > h2, :scope > .vh");
 if(!h||!p.textContent.trim())return;
 const w=document.createElement("span");w.className="hintw";w.innerHTML='<button type="button" class="hintb" aria-label="Пояснение">?</button>';
 const box=document.createElement("div");box.className="hintbox";box.innerHTML=p.innerHTML;w.appendChild(box);p.remove();h.appendChild(w);
 const sec=w.closest("section"),place=()=>{const r=w.getBoundingClientRect(),bw=Math.min(460,innerWidth-48);
   box.style.left=Math.min(-8,innerWidth-24-r.left-bw)+"px";if(sec){sec.style.position="relative";sec.style.zIndex=30}};
 const done=()=>{if(sec&&!w.classList.contains("open"))sec.style.zIndex=""};
 w.addEventListener("mouseenter",place);w.addEventListener("mouseleave",done);
 w.querySelector("button").onclick=e=>{e.stopPropagation();const on=!w.classList.contains("open");
  document.querySelectorAll(".hintw.open").forEach(x=>x.classList.remove("open"));if(on){place();w.classList.add("open")}else done()}});
addEventListener("click",e=>{if(!e.target.closest(".hintw"))document.querySelectorAll(".hintw.open").forEach(x=>{x.classList.remove("open");const s=x.closest("section");if(s)s.style.zIndex=""})});
</script>""".replace("__S__", json.dumps(load_searches(), ensure_ascii=False).replace("</", "<\\/")) \
        .replace("__SUG__", json.dumps(sug, ensure_ascii=False).replace("</", "<\\/")) \
        .replace("__QST__", json.dumps(state.get("qstats", {}), ensure_ascii=False).replace("</", "<\\/")) \
        .replace("__QE__", json.dumps(state.get("qempty", {}), ensure_ascii=False).replace("</", "<\\/")) \
        .replace("__LIVE__", json.dumps(live, ensure_ascii=False).replace("</", "<\\/"))
    return f'<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Главная</title>{SIDE_CSS}{css}<main>{body}</main>{js}'


COST_FIELDS = [   # поле, подпись, единица; доли показываем в процентах
    ("rate", "Курс ¥ ЦБ / рынок (если не авто)", "₽"), ("pay_rate", "Курс KwikPay — по нему платите", "₽"),
    ("rate_markup", "Наценка к курсу при оплате", "%"), ("buyer_fee", "Комиссия байера", "%"),
    ("protect", "Защита от рисков", "%"), ("cn_ship_cny", "Доставка по Китаю", "¥"), ("intl_per_kg", "Международная доставка", "₽/кг"),
    ("intl_min", "Минимум за посылку", "₽"), ("ru_ship", "Доставка по РФ", "₽"), ("photo", "Фотоотчёт и сборы", "₽"),
    ("expert_cny", "Экспертиза", "¥"), ("duty_free_eur", "Беспошлинный лимит", "€"), ("duty_rate", "Пошлина с превышения", "%"),
    ("eur_rate", "Курс € (если не авто)", "₽"), ("other", "Прочее / возвраты от продажи", "%"), ("target", "Целевая маржа («Брать до»)", "%"),
    ("alert_min", "Уведомлять о новой вещи от прибыли", "₽"), ("cn_days", "Срок доставки из Китая до вас (для заказов «к дате»)", "дн.")]
JP_FIELDS = [   # Япония (Mercari) через посредника — OZTA
    ("jpy_rate", "Курс иены (если не авто)", "₽"), ("usd_rate", "Курс $ (если не авто)", "₽"), ("jp_markup", "Наценка к курсу иены", "%"),
    ("jp_fee", "Комиссия за выкуп", "%"), ("jp_min_usd", "Минимум комиссии и сбора", "$"), ("jp_dom", "Доставка по Японии", "¥"),
    ("jp_intl_kg", "Перевозчик до Владивостока — уточнить", "¥/кг"), ("jp_ship_fee", "Сбор посредника к доставке", "%"),
    ("jp_ru_ship", "Владивосток → Бердск (СДЭК) — уточнить", "₽"), ("jp_extra", "Прочее за посылку", "₽"),
    ("jp_feed_min", "В ленту — от прибыли", "₽"), ("jp_days", "Срок доставки из Японии до вас (для заказов «к дате»)", "дн.")]
WEIGHT_NAMES = {"sneakers": "кроссовки", "shoes": "обувь", "outerwear": "верхняя одежда", "clothing": "одежда", "bag": "сумки",
                "accessories": "аксессуары", "jewelry": "украшения", "?": "тип не ясен"}


def deals_page(deals: list[dict], cat: dict, tax: float, rate_note: str = "") -> str:
    """Вкладка «Сделки»: купленное → в пути → у меня → продано, реальная прибыль, ставки калькулятора."""
    e = html_escape
    fmt = lambda v: f"{v:,.0f}".replace(",", " ")
    nums = {d["deal_id"]: deal_numbers(d) for d in deals}
    held = [d for d in deals if d["status"] != "продано"]
    sold = [d for d in deals if d["status"] == "продано" and d.get("sold_rub")]
    real = sum(nums[d["deal_id"]]["profit"] for d in sold)
    days = [(datetime.fromisoformat(d["ts_sold"]) - datetime.fromisoformat(d["ts_buy"])).days for d in sold if d.get("ts_sold")]
    plan_hit = [nums[d["deal_id"]]["profit"] / d["profit_rub"] for d in sold if d.get("profit_rub")]
    exp = [d for d in deals if d.get("expert")]
    tiles = [("вложено в товар на руках", f'{fmt(sum(nums[d["deal_id"]]["cost"] for d in held))} ₽'), ("вещей в обороте", len(held)),
             ("экспертиз · из них подделок", f'{len(exp)} · {sum(d["expert"] == "подделка" for d in exp)}'),
             ("продано", len(sold)), ("реальная прибыль", f'{"+" if real > 0 else ""}{fmt(real)} ₽'),
             ("средний срок продажи", f"{statistics.mean(days):.0f} дн." if days else "—"),
             ("факт к прогнозу", f"{100 * statistics.median(plan_hit):.0f}%" if plan_hit else "—")]

    def thumb(u):
        u = "https:" + u if u.startswith("//") else u
        return u + "_200x200.jpg" if "alicdn.com" in u and not re.search(r"_\d+x\d+", u) else u

    def inp(d, field, val, ph="", w=""):
        return (f'<input class="di{w}" type="number" min="0" data-id="{d["deal_id"]}" data-f="{field}" '
                f'value="{"" if val in (None, "") else round(val)}" placeholder="{ph}">')
    rows = []
    for d in deals:
        n = nums[d["deal_id"]]
        st = "".join(f'<button class="st{" on" if s == d["status"] else ""}" data-id="{d["deal_id"]}" data-s="{s}">{s}</button>' for s in DEAL_STATUSES)
        prof = n["profit"]
        pcell = (f'<b class="{"pos" if prof > 0 else "neg"}">{"+" if prof > 0 else ""}{fmt(prof)} ₽</b>' if prof is not None else
                 f'<span class="off">прогноз {"+" if (n["plan"] or 0) > 0 else ""}{fmt(n["plan"] or 0)} ₽</span>')
        rows.append(
            f'<tr data-row="{d["deal_id"]}"><td><a href="{e(d.get("url", ""))}" target="_blank" class="dph">'
            + (f'<img src="{e(thumb(d.get("image", "")))}" referrerpolicy="no-referrer" loading="lazy" alt="">' if d.get("image") else "")
            + f'</a></td><td><b>{e(d.get("brand", ""))}</b> <span class="off">{e(d.get("model") or d.get("category", ""))}</span>'
            f'<div class="dt" title="{e(d.get("title", ""))}">{e(d.get("title", ""))}</div><div class="off sm">куплено {e(d["ts_buy"][:10])}'
            + (f' · продано {e(d["ts_sold"][:10])}' if d.get("ts_sold") else "") + f'</div><div class="sts">{st}</div>'
            + '<div class="sts ex" title="Результат проверки подлинности. По этим записям Щебень потом сверит, какие признаки риска действительно предсказывают подделку">'
              '<span class="off sm">экспертиза:</span>'
            + "".join(f'<button class="st{" on" if d.get("expert") == x else ""}" data-id="{d["deal_id"]}" data-x="{x}">{x}</button>'
                      for x in EXPERT_RESULTS) + '</div></td>'
            f'<td class="n">¥{fmt(d.get("price_cny", 0))}<div class="off sm">курс {d.get("rate", 0):.2f}</div></td>'
            f'<td>{inp(d, "cost_real", d.get("cost_real"), fmt(d.get("cost_rub", 0)))}<div class="off sm">прогноз {fmt(d.get("cost_rub", 0))}</div></td>'
            f'<td>{inp(d, "sold_rub", d.get("sold_rub"), fmt(d.get("sell_rub") or 0))}<div class="off sm">прогноз {fmt(d.get("sell_rub") or 0)}</div></td>'
            f'<td>{inp(d, "sold_fee", d.get("sold_fee"), "0", " sm")}<input class="di tx" data-id="{d["deal_id"]}" data-f="channel" '
            f'value="{e(d.get("channel", ""))}" placeholder="Авито / TG / Oskelly"></td>'
            f'<td class="n">{pcell}</td><td><button class="del" data-id="{d["deal_id"]}" title="Удалить запись">✕</button></td></tr>')
    table = ('<div class="box"><table class="dl"><tr><th></th><th>Вещь</th><th>Цена</th><th>Затраты факт, ₽</th><th>Продал за, ₽</th>'
             '<th>Комиссия, ₽ · канал</th><th>Прибыль</th><th></th></tr>' + "".join(rows) + "</table></div>") if rows else \
        '<p class="note">Пока пусто. В ленте на карточке нажмите «Купил» — вещь появится здесь с ценой, курсом и прогнозом.</p>'

    by: dict = {}
    for d in sold:
        by.setdefault(d.get("brand", "?"), []).append(d)
    brand_rows = "".join(
        f'<tr><td>{e(b)}</td><td class=n>{len(ds)}</td><td class=n>{fmt(statistics.mean(d.get("sell_rub") or 0 for d in ds))} ₽</td>'
        f'<td class=n>{fmt(statistics.mean(d["sold_rub"] for d in ds))} ₽</td>'
        f'<td class=n>{fmt(sum(nums[d["deal_id"]]["profit"] for d in ds))} ₽</td></tr>'
        for b, ds in sorted(by.items(), key=lambda x: -len(x[1])))
    brands = (f'<section><h2>По брендам: прогноз и факт</h2><p class="note">С первой продажи лента берёт цену продажи из ваших сделок '
              f'(та же модель, иначе тот же бренд и тип вещи) — с пометкой «по вашим продажам».</p><div class="box"><table><tr><th>Бренд</th>'
              f'<th>Продано</th><th>Прогноз продажи</th><th>Факт продажи</th><th>Прибыль</th></tr>{brand_rows}</table></div></section>') if brand_rows else ""

    c = COSTS
    c_w = list(COSTS["weights"])
    fields = "".join(
        f'<label>{e(t)}<span><input class="cf" data-k="{k}" data-u="{u}" type="number" step="any" '
        f'value="{round(c[k] * 100, 3) if u == "%" else c[k]}"><i>{u}</i></span></label>' for k, t, u in COST_FIELDS)
    jp_fields = "".join(
        f'<label>{e(t)}<span><input class="cf" data-k="{k}" data-u="{u}" type="number" step="any" '
        f'value="{round(c[k] * 100, 3) if u == "%" else c[k]}"><i>{u}</i></span></label>' for k, t, u in JP_FIELDS)
    weights = "".join(f'<label>{e(WEIGHT_NAMES.get(k, k))}<span><input class="cw" data-k="{k}" type="number" step="0.1" value="{v}"><i>кг</i></span></label>'
                      for k, v in c["weights"].items())
    src = "ЦБ" if c.get("rate_src", "ЦБ") in ("ЦБ", "") else "рыночный (ЦБ недоступен через VPN)"
    auto = (f'Курс {src} на {e(c["rate_date"])}: ¥ {c["rate"]}, € {c["eur_rate"]}' if c.get("rate_auto") and c.get("rate_date") else "Курс вписан вручную")
    # курс KwikPay: из него наценка к курсу ЦБ/рынка; курс обновляется каждый день, наценка остаётся
    auto += (f' · KwikPay: вписан {c["pay_rate"]} ₽ ({e(c.get("pay_date", ""))}), наценка {c["rate_markup"] * 100:.2f}% — '
             f'сегодня считаю по {eff_rate():.4f} ₽ за ¥' if c.get("pay_rate") else
             ' · впишите курс KwikPay — наценка к курсу посчитается сама и будет держаться при обновлении курса')
    opts = "".join(f'<button class="st{" on" if k == "sneakers" else ""}" data-w="{k}">{e(WEIGHT_NAMES.get(k, k))}</button>' for k in c["weights"])
    body = f"""<section class="top"><div><h1>Сделки</h1><p class="note">Учёт покупок: отмечайте статус и вписывайте фактические цифры —
 прибыль считается по факту, а цены продажи в ленте подстраиваются под ваши продажи.</p></div></section>
<div class="tiles">{"".join(f'<div class="tile"><b>{v}</b><span>{k}</span></div>' for k, v in tiles)}</div>
<section><h2>Покупки</h2>{table}
 <h3>Записать покупку вручную</h3><p class="note">Если купили не через кнопку «Купил» в ленте. Вставьте ссылку на вещь — если Щебень её видел, бренд и тип подставятся сами.</p>
 <div class="mrow"><input id="q_url" class="di tx w" placeholder="ссылка на вещь (Goofish или Mercari) или название"><input id="q_brand" class="di tx" placeholder="бренд">
  <select id="q_cat" class="di tx">{"".join(f'<option value="{k}">{e(WEIGHT_NAMES.get(k, k))}</option>' for k in c_w)}</select>
  <select id="q_site" class="di tx"><option value="">Goofish, ¥ юани</option><option value="mercari">Mercari, ¥ иены</option></select>
  <input id="q_price" class="di" type="number" min="0" placeholder="цена ¥"><input id="q_sell" class="di" type="number" min="0" placeholder="продам за, ₽">
  <button class="btn go2" id="q_add">Записать</button></div></section>
<section><h2>Цены России — что видел сам</h2><p class="note">Самые ценные данные: за сколько вещь реально продаётся у нас. Впишите, что видели на Avito, Oskelly или в канале
 (или за сколько продали сами) — запись попадёт в «Цены России» и будет учитываться наравне с постами каналов.</p>
 <div class="mrow"><input id="r_what" class="di tx w" placeholder="бренд и модель, например Prada нейлон мессенджер"><input id="r_cond" class="di tx" placeholder="состояние">
  <input id="r_price" class="di" type="number" min="0" placeholder="цена, ₽"><input id="r_days" class="di sm" type="number" min="0" placeholder="дней висит">
  <select id="r_where" class="di tx"><option>avito</option><option>oskelly</option><option>telegram</option><option>своя продажа</option></select>
  <label class="chk"><input type="checkbox" id="r_sold"> продано</label><button class="btn go2" id="r_add">Записать</button><span class="note" id="r_msg"></span></div></section>
<section><h2>Корзина Японии <span class="off sm" id="bk_n"></span></h2><p class="note">Несколько вещей Mercari одной посылкой: комиссия за выкуп берётся с каждой вещи, а доставка с её минимумом $20,
 пошлина и путь по России — один раз. Добавляйте вещи кнопкой «В корзину» на карточке Mercari в ленте (в плотной ленте — под «⋯»).</p><div id="bk"></div></section>
{brands}
<section class="two"><div><h2>Калькулятор</h2><div class="box pad"><div class="cin">
 <label>Цена, ¥<input id="m_p" type="number" min="0" value="2000"></label><label>Продам за, ₽<input id="m_s" type="number" min="0" value="35000"></label></div>
 <div class="sts" id="m_w">{opts}</div><div id="m_out"></div></div></div>
<div><h2>Ставки</h2><div class="box pad"><p class="note">{e(auto)}{(" · " + e(rate_note)) if rate_note else ""}
 <label class="chk"><input type="checkbox" id="rate_auto" {"checked" if c.get("rate_auto") else ""}> брать курс автоматически (ЦБ, а если он недоступен — рыночный)</label>
 <button class="btn" id="rate_now">обновить курс</button></p>
 <div class="cfg">{fields}</div><h3>Япония (Mercari) — посредник OZTA</h3>
 <p class="note">С сайта OZTA (ozta.com): выкуп — 10% от цены вещи, не меньше $20; доставка — тариф перевозчика + 10%, не меньше $20
 (минимумы в иенах сейчас ≈ ¥{fmt(jp_min_jpy())}). Тариф перевозчика и доставку до Бердска OZTA не публикует — в полях оценки:
 море до Владивостока ≈ ¥2 000/кг, СДЭК Владивосток → Бердск ≈ 1 100 ₽. Получите расчёт у OZTA и впишите сюда.
 Несколько вещей в одной посылке делят минимум $20 за доставку — выгоднее копить заказ. На Mercari цена обычно уже с доставкой по Японии (送料込み).
 Беспошлинный лимит — общий, из ставок выше.</p>
 <div class="cfg">{jp_fields}</div><h3>Вес посылки по типу вещи</h3><div class="cfg">{weights}</div>
 <p class="note" id="c_msg">Изменения сохраняются сразу и пересчитывают ленту. Беспошлинный лимит проверьте по актуальным правилам ФТС.</p></div></div></section>"""
    data = json.dumps({"costs": c, "channels": cat.get("channels") or [], "tax": tax}, ensure_ascii=False)
    css = """<style>
main{max-width:1400px}.box.pad{padding:14px}.sm{font-size:11.5px}.pos{color:var(--good)}.neg{color:var(--bad)}h3{font-size:13px;margin:14px 0 6px}
.dl td{vertical-align:middle}.dph img{width:64px;height:64px;object-fit:cover;border-radius:10px;display:block;background:var(--soft)}
.dt{max-width:360px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:12.5px}
.sts{display:flex;flex-wrap:wrap;gap:4px;margin-top:6px}.st{border:1px solid var(--line);background:var(--soft);color:var(--muted);border-radius:999px;
 padding:2px 10px;font:inherit;font-size:12px;cursor:pointer}.st.on{background:var(--accent);color:var(--bg);border-color:transparent}
.di,.cin input,.cfg input{background:var(--soft);border:1px solid var(--line);border-radius:8px;padding:6px 8px;color:var(--fg);font:600 13px "IBM Plex Mono",Consolas,monospace;width:110px}
.di.sm{width:80px}.di.tx{display:block;margin-top:4px;width:160px;font-family:inherit;font-weight:400}.di:focus,.cfg input:focus{outline:2px solid var(--accent)}
.del{background:none;border:0;color:var(--muted);cursor:pointer;font-size:15px}.del:hover{color:var(--bad)}
.two{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.4fr);gap:18px}@media (max-width:900px){.two{grid-template-columns:1fr}}
.cin{display:grid;grid-template-columns:1fr 1fr;gap:10px}.cin label,.cfg label{display:grid;gap:3px;font-size:12px;color:var(--muted)}.cin input{width:100%}
.cfg{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:10px}.cfg span{display:flex;align-items:center;gap:6px}.cfg input{flex:1;min-width:0}
.cfg i{font-style:normal;color:var(--muted);min-width:28px}.chk{display:inline-flex;gap:6px;align-items:center;margin:0 10px}
.btn{border:1px solid var(--line);background:var(--soft);color:var(--fg);border-radius:999px;padding:4px 12px;font:inherit;font-size:12.5px;cursor:pointer}
.ct{width:100%;margin-top:10px}.ct td:last-child{text-align:right;font-family:"IBM Plex Mono",Consolas,monospace;white-space:nowrap}
.ct .tt td{font-weight:700;border-bottom:0}.ct .ok td{color:var(--good)}.ct .no td{color:var(--bad)}
.mrow{display:flex;flex-wrap:wrap;gap:8px;align-items:center}.mrow .di.tx{display:inline-block;margin:0;width:150px}.mrow .di.tx.w{width:min(100%,360px)}
.go2{background:var(--accent);color:var(--bg);border-color:transparent;font-weight:600}.ex{align-items:center}
.bkg{display:grid;grid-template-columns:minmax(0,1.3fr) minmax(0,1fr);gap:14px}@media (max-width:900px){.bkg{grid-template-columns:1fr}}
.bki{display:flex;gap:10px;align-items:center;padding:6px 0;border-bottom:1px solid var(--line);font-size:13px}.bki img{width:48px;height:48px;object-fit:cover;border-radius:8px;background:var(--soft)}
.bki a{color:var(--fg);text-decoration:none;flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.bki b{font-family:"IBM Plex Mono",Consolas,monospace;white-space:nowrap}
</style>"""
    js = """<script>
const D=__DATA__,$=id=>document.getElementById(id),fmt=v=>Math.round(v).toLocaleString("ru-RU"),
 esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const post=(u,b)=>fetch(u,{method:"POST",headers:{"Content-Type":"application/json","X-Goofish-UI":"1"},body:JSON.stringify(b)})
 .then(r=>r.json().then(j=>{if(!r.ok)throw j.error||"ошибка";return j}));
function costParts(pc,k){const c=D.costs,r=c.rate*(1+c.rate_markup),g=pc*r,w=c.weights||{},kg=w[k]??w["?"]??1,over=g/Math.max(c.eur_rate,1)-c.duty_free_eur;
 return [["вещь",g],["комиссия байера",g*c.buyer_fee],["защита от рисков",g*c.protect],["доставка по Китаю",c.cn_ship_cny*r],
  [`международная доставка, ${kg} кг`,Math.max(kg*c.intl_per_kg,c.intl_min)],["пошлина",Math.max(over,0)*c.duty_rate*c.eur_rate],
  ["доставка по РФ",c.ru_ship],["фотоотчёт и сборы",c.photo],["экспертиза",c.expert_cny*r]]}
function bestProfit(cost,sell){let b=null;for(const ch of (D.channels.length?D.channels:[{name:"продажа"}])){const g=sell*(ch.price_factor??1),
 pr=g*(1-(ch.fee_rate||0))-(ch.fixed_fee_rub||0)-g*((D.tax||0)+D.costs.other)-cost;if(!b||pr>b.p)b={p:pr,ch:ch.name}}return b}
let W="sneakers";
function calc(){const pc=+$("m_p").value||0,s=+$("m_s").value||0,parts=costParts(pc,W),cost=parts.reduce((a,x)=>a+x[1],0),b=s?bestProfit(cost,s):null;
 $("m_out").innerHTML=`<table class="ct">${parts.filter(x=>Math.round(x[1])).map(x=>`<tr><td>${esc(x[0])}</td><td>${fmt(x[1])} ₽</td></tr>`).join("")}
 <tr class="tt"><td>Затраты всего</td><td>${fmt(cost)} ₽</td></tr>${b?`<tr class="tt ${b.p>0?"ok":"no"}"><td>Прибыль · ${esc(b.ch)}</td><td>${b.p>0?"+":""}${fmt(b.p)} ₽ · ${Math.round(100*b.p/Math.max(cost,1))}%</td></tr>`:""}</table>`}
$("m_p").oninput=$("m_s").oninput=calc;
$("m_w").onclick=e=>{const b=e.target.closest("[data-w]");if(!b)return;W=b.dataset.w;$("m_w").querySelectorAll(".st").forEach(x=>x.classList.toggle("on",x===b));calc()};
calc();
// сделки: статус, фактические цифры, удаление
document.querySelectorAll(".dl .st").forEach(b=>b.onclick=()=>post("/deal/update",{deal_id:+b.dataset.id,status:b.dataset.s}).then(()=>location.reload()).catch(alert));
document.querySelectorAll(".di").forEach(i=>i.onchange=()=>post("/deal/update",{deal_id:+i.dataset.id,[i.dataset.f]:i.type==="number"?(i.value===""?null:+i.value):i.value})
 .then(()=>location.reload()).catch(alert));
document.querySelectorAll(".del").forEach(b=>b.onclick=()=>{if(confirm("Удалить запись о сделке?"))post("/deal/update",{deal_id:+b.dataset.id,delete:true}).then(()=>location.reload()).catch(alert)});
// ставки: сохраняются сразу
let T=0;
function saveCosts(){clearTimeout(T);T=setTimeout(()=>{const c={rate_auto:$("rate_auto").checked,weights:{}};
 document.querySelectorAll(".cf").forEach(i=>{if(i.value!=="")c[i.dataset.k]=i.dataset.u==="%"?+i.value/100:+i.value});
 document.querySelectorAll(".cw").forEach(i=>{if(i.value!=="")c.weights[i.dataset.k]=+i.value});
 post("/costs",c).then(j=>{D.costs=j.costs;calc();$("c_msg").textContent="Сохранено, лента пересчитана.";
  // курс и наценку сервер мог пересчитать (вписан курс KwikPay) — показываем, что теперь в силе
  document.querySelectorAll(".cf").forEach(i=>{if(i!==document.activeElement&&["rate","eur_rate","rate_markup","pay_rate","jpy_rate","usd_rate"].includes(i.dataset.k))
   i.value=i.dataset.u==="%"?Math.round(j.costs[i.dataset.k]*1e5)/1e3:j.costs[i.dataset.k]})}).catch(e=>$("c_msg").textContent="Не сохранилось: "+e)},500)}
document.querySelectorAll(".cf,.cw").forEach(i=>i.oninput=saveCosts);$("rate_auto").onchange=saveCosts;
$("rate_now").onclick=()=>post("/costs",{refresh_rate:true}).then(()=>location.reload()).catch(alert);
// экспертиза: повторное нажатие снимает отметку
document.querySelectorAll(".ex .st").forEach(b=>b.onclick=()=>post("/deal/update",{deal_id:+b.dataset.id,expert:b.classList.contains("on")?"":b.dataset.x}).then(()=>location.reload()).catch(alert));
// покупка вручную
$("q_add").onclick=()=>{const b={url:$("q_url").value.trim(),brand:$("q_brand").value.trim(),ckey:$("q_cat").value,site:$("q_site").value,price:+$("q_price").value,sell:+$("q_sell").value||null};
 if(!b.price||!(b.url||b.brand)){alert("Нужны цена и ссылка (или бренд)");return}post("/deal/manual",b).then(()=>location.reload()).catch(alert)};
// цена России вручную
$("r_add").onclick=()=>{const b={what:$("r_what").value.trim(),cond:$("r_cond").value.trim(),price:+$("r_price").value,days:+$("r_days").value||0,where:$("r_where").value,sold:$("r_sold").checked};
 if(!b.what||!b.price){alert("Нужны бренд/модель и цена");return}
 post("/market/manual",b).then(j=>{$("r_msg").textContent=j.parsed?`Записано: ${j.parsed}`:"Записано, но бренд не распознан — проверьте написание (как в каталоге)";
  ["r_what","r_cond","r_price","r_days"].forEach(i=>$(i).value="");$("r_sold").checked=false}).catch(alert)};
// корзина Японии
function bkDraw(B){$("bk_n").textContent=B.items.length?`· ${B.items.length} вещ.`:"";
 if(!B.items.length){$("bk").innerHTML='<p class="note">Корзина пуста.</p>';return}
 const k=B.calc,d=k.together!=null&&k.alone!=null?k.together-k.alone:null;
 $("bk").innerHTML=`<div class="bkg"><div class="box pad">${B.items.map(x=>`<div class="bki">${x.img?`<img src="${esc(x.img)}" referrerpolicy="no-referrer" loading="lazy" alt="">`:""}
   <a href="${esc(x.url)}" target="_blank" title="${esc(x.title)}">${esc(x.name)}</a><b>¥${fmt(x.yen)} JP</b>
   <b class="${x.p>0?"pos":"neg"}" title="Прибыль, если везти эту вещь отдельной посылкой">${x.p==null?"?":(x.p>0?"+":"")+fmt(x.p)+" ₽"}</b>
   <button class="del" data-bk="${esc(x.id)}" title="Убрать из корзины">✕</button></div>`).join("")}</div>
  <div class="box pad"><table class="ct">${k.parts.map(p=>`<tr><td>${esc(p[0])}</td><td>${fmt(p[1])} ₽</td></tr>`).join("")}
   <tr class="tt"><td>Затраты на посылку</td><td>${fmt(k.cost)} ₽</td></tr>
   ${k.together!=null?`<tr><td>Выручка после комиссий продажи</td><td>${fmt(k.payout)} ₽</td></tr>
   <tr class="tt ${k.together>0?"ok":"no"}"><td>Прибыль одной посылкой</td><td>${k.together>0?"+":""}${fmt(k.together)} ₽</td></tr>
   <tr><td>Если везти каждую отдельно</td><td>${k.alone>0?"+":""}${fmt(k.alone)} ₽</td></tr>
   <tr class="tt ${d>0?"ok":"no"}"><td>Выгода от сборной посылки</td><td>${d>0?"+":""}${fmt(d)} ₽</td></tr>`
   :`<tr><td colspan="2" class="note">У ${k.unknown} вещ. нет цены продажи — прибыль посылки не посчитать.</td></tr>`}</table>
   <p class="note">Тариф перевозчика и доставка до Бердска — оценки, пока OZTA не прислал свои цифры.</p></div></div>`;
 document.querySelectorAll("[data-bk]").forEach(b=>b.onclick=()=>post("/basket",{id:b.dataset.bk,on:false}).then(bkDraw).catch(alert))}
fetch("/basket").then(r=>r.json()).then(bkDraw).catch(()=>{$("bk").innerHTML='<p class="note">Корзина не загрузилась.</p>'});
</script>""".replace("__DATA__", data.replace("</", "<\\/"))
    return (f'<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Сделки</title>'
            f'{SIDE_CSS}{css}<main>{body}</main>{js}')


PHONE_SECTION = """<section><h2>Доступ с телефона</h2><div class="box" style="padding:12px 14px;display:grid;gap:10px">
<p class="note" style="margin:0">Лента, «Главная» и заказы — с телефона откуда угодно, через Tailscale (закрытая сеть только ваших устройств).
1) Установите Tailscale на компьютер и телефон и войдите в один аккаунт. 2) Нажмите «Включить» — Windows спросит про доступ Python к сети:
оставьте только «Частные сети». 3) Отсканируйте QR телефоном (или пришлите себе ссылку) — дальше телефон помнит ключ.
Без ключа приложение никого не пускает; «Выключить» сразу отзывает ключ.</p>
<div id="ph_st" class="note" style="margin:0">…</div><div id="ph_qr" style="background:#fff;padding:10px;border-radius:10px;width:max-content" hidden></div>
<div><button id="ph_btn" type="button" style="border:0;border-radius:999px;padding:8px 16px;background:var(--accent);color:var(--bg);font:inherit;font-weight:600;cursor:pointer"></button></div></div></section>
<script src="https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js"></script>
<script>(()=>{const $=id=>document.getElementById(id);let ON=false,URL_="";
function draw(j){ON=j.on;$("ph_btn").textContent=ON?"Выключить и отозвать ключ":"Включить доступ с телефона";
 $("ph_st").innerHTML=!ON?"Выключен.":j.running&&j.url?`Работает. Ссылка с ключом (не пересылайте посторонним): <code style="user-select:all;word-break:break-all">${j.url}</code>`
  :j.err?"Ждёт: "+j.err:"Включается…";
 if(j.url!==URL_){URL_=j.url;$("ph_qr").innerHTML="";$("ph_qr").hidden=!(j.running&&j.url);
  if(j.running&&j.url&&window.QRCode)new QRCode($("ph_qr"),{text:j.url,width:180,height:180})}}
const load=()=>fetch("/phone").then(r=>r.json()).then(draw).catch(()=>{});
$("ph_btn").onclick=()=>{if(ON&&!confirm("Выключить доступ? Ссылка на телефоне перестанет работать."))return;
 fetch("/phone",{method:"POST",headers:{"Content-Type":"application/json","X-Goofish-UI":"1"},body:JSON.stringify({on:!ON})}).then(()=>setTimeout(load,1500))};
load();setInterval(load,4000)})()</script>"""


def side_page(kind: str, state: dict, cat: dict, prices: dict, outdir: Path) -> str:
    """Вкладки приложения «Статус», «Каталог», «Цены» (пока только просмотр)."""
    e = html_escape
    fmt = lambda v: f"{v:,.0f}".replace(",", " ")
    if kind == "status":
        tiles = [("ответов Goofish", state.get("ext", 0)), ("в базе", state.get("total", 0)),
                 ("новых за сеанс", state.get("new", 0)), ("из них в ленте", state.get("new_good", 0)),
                 ("в ленте всего", state.get("good", 0)), ("оценка 75+", state.get("top", 0))]
        qrows = "".join(f"<tr><td>{e(q)}</td><td class=n>{v['pages']}</td><td class=n>{v['items']}</td>"
                        f"<td class=n>{v['new']}</td><td class=n>{e(v.get('at', ''))}</td></tr>"
                        for q, v in reversed(list(state.get("queries", {}).items())))
        log = log_dir() / "sheben.log"       # журнал с датами и ротацией (gp_log)
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()[-40:] if log.exists() else []
        tail = "\n".join(reversed(lines)) or "журнал пуст"   # свежее сверху
        body = (f'<h1>Статус</h1><p class="note">Обновляется каждые 5 секунд.</p>'
                f'<div class="tiles">{"".join(f"<div class=tile><b>{v}</b><span>{k}</span></div>" for k, v in tiles)}</div>'
                f'<section><h2>Запросы за сеанс</h2><div class="box"><table><tr><th>Запрос</th><th>Страниц</th><th>Объявлений</th>'
                f'<th>Новых</th><th>Когда</th></tr>{qrows or "<tr><td colspan=5 class=off>Пока ничего: поищите на вкладке Goofish</td></tr>"}'
                f'</table></div></section>'
                + PHONE_SECTION
                + (lambda ck: f'<section><h2>Проверка фото ИИ (Gemini)</h2><div class="box" style="padding:12px 14px"><p class="note" style="margin:0">'
                   + (f'Включена · модель {e(ck.status.get("model", ""))} · сегодня {ck.status.get("today", 0)} из {ck.status.get("limit", "?")} · в очереди {len(ck.queue)}'
                      if ck.status.get("enabled") else "Выключена: впишите ключ Gemini в файл .env (папка программы, строка GEMINI_API_KEY=), затем перезапустите приложение.")
                   + (f'<br>Последняя ошибка: {e(ck.status["error"])}' if ck.status.get("error") and ck.status.get("enabled") else "")
                   + '</p></div></section>' if ck else "")(state.get("checker"))
                + (lambda s: f'<section><h2>Telegram-бот</h2><div class="box" style="padding:12px 14px"><p class="note" style="margin:0">'
                   + ((f'Токен вписан · чат {"найден" if s.get("tg_chat_id") else "ещё не найден — напишите боту /start"}'
                       f' · последняя отправка: {e(state.get("tg_status", "ещё не было"))}'
                       f'<br><button onclick="fetch(\'/tg/test\',{{method:\'POST\',headers:{{\'X-Goofish-UI\':\'1\'}},body:\'{{}}\'}})'
                       f'.then(()=>this.textContent=\'отправлено — смотрите Telegram\')">Отправить проверку</button>')
                      if s.get("tg_bot_token") else
                      "Выключен. 1) В Telegram откройте @BotFather → /newbot и получите токен. 2) Впишите его в файл .env "
                      "(папка программы) в строку TG_BOT_TOKEN=. 3) Напишите своему боту /start и перезапустите приложение.")
                   + '</p></div></section>')(ai_settings())
                + (lambda m: f'<section><h2>Рынок (цены из TG-каналов)</h2><div class="box" style="padding:12px 14px"><p class="note" style="margin:0">'
                   f'Каналов: {len(m["channels"])} · последний сбор: {e(m["last_run"][:16].replace("T", " ") or "не было")}'
                   f'{" · идёт сбор" if state.get("market_running") else ""} · импорт из Telegram Desktop: '
                   f'{e((m["import_last"][:16].replace("T", " ") + " — " + m["import_note"]) if m["import_last"] else "не было")}'
                   f'. Настройки — на вкладке «Цены».</p></div></section>')(load_market_cfg())
                + (f'<section><h2>Продавцы реплик ({len(SELLER_BLACKLIST)})</h2><p class="note">Их объявления не показываются в ленте. '
                   f'Список пересчитывается сам: по вещам в базе, по открытым вами профилям и по вашим ✕ «подделка».</p>'
                   f'<div class="box"><table><tr><th>Продавец</th><th>Вещей в базе</th><th>Почему</th></tr>'
                   + "".join(f'<tr><td>{e(s["seller"])}</td><td class=n>{s["n"]}</td><td>{e(s["why"])}</td></tr>'
                             for s in sorted(SELLER_BLACKLIST.values(), key=lambda s: -s["n"]))
                   + '</table></div></section>' if SELLER_BLACKLIST else "")
                + f'<section><h2>Журнал</h2><div class="box"><pre>{e(tail)}</pre></div></section>')
        return f'<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="5"><title>Статус</title>{SIDE_CSS}<main>{body}</main>'
    return EDITOR_HTML.replace("__MODE__", kind).replace("__CSS__", SIDE_CSS)


__all__ = [n for n in dir() if not n.startswith("__")]   # с «_именами»: модули ниже берут их через *
