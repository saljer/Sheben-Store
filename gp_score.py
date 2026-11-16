"""Ставки и сделки, оценка объявления (evaluate), вне каталога, база SQLite, правила из скрытий."""
from __future__ import annotations

from gp_parse import *  # noqa: F401,F403


# ---------------------------------------------------------------- ставки (costs.json, вкладка «Сделки»)
COSTS_FILE = Path(__file__).with_name("costs.json")
COST_DEFAULTS = {
    "rate_auto": True, "rate": 12.8981, "eur_rate": 100.0, "rate_date": "",   # курсы ЦБ, обновляются раз в день
    "rate_src": "",             # откуда курс: «ЦБ» или «рынок» (ЦБ недоступен — VPN)
    "rate_markup": 0.0,         # наценка к курсу ЦБ при оплате (карта/обменник), доля
    "pay_rate": 0.0, "pay_date": "",    # курс, по которому вы платите (KwikPay): из него считается наценка
    # Япония (Mercari) через посредника — OZTA (ozta.com): выкуп 10%, не меньше $20; к тарифу перевозчика +10%,
    # не меньше $20. Тариф перевозчика и доставку по РФ OZTA не публикует — ниже оценки, уточните у них
    "jp_profile": "OZTA",
    "jpy_rate": 0.51,           # ₽ за 1 японскую иену (обновляется вместе с курсом юаня)
    "usd_rate": 80.0,           # ₽ за доллар — минимумы OZTA в долларах
    "jp_markup": 0.0,           # наценка посредника к курсу иены при оплате в рублях, доля
    "jp_fee": 0.10, "jp_fee_min": 1000.0,   # комиссия за выкуп: доля от цены вещи, но не меньше (¥, если не задан $)
    "jp_min_usd": 20.0,         # минимум комиссии за выкуп и сбора за доставку, $ (0 — брать jp_fee_min в иенах)
    "jp_dom": 0.0,              # доставка по Японии до склада посредника, ¥ (на Mercari обычно уже в цене — 送料込み)
    "jp_intl_kg": 2000.0,       # тариф перевозчика Япония -> Владивосток, ¥ за кг (оценка: море ≈ ¥2 000/кг)
    "jp_ship_fee": 0.10,        # сбор посредника к тарифу перевозчика, доля (не меньше минимума)
    "jp_ru_ship": 1100.0,       # Владивосток -> Бердск (СДЭК, склад–склад), ₽ — оценка
    "cn_days": 0.0, "jp_days": 0.0,     # сколько дней вещь едет до вас из Китая / Японии (0 — не задано): для «нужно к дате» в заказах
    "jp_extra": 0.0,            # прочее за посылку (фото, упаковка, страховка), ₽
    "buyer_fee": 0.0,           # комиссия байера/посредника, доля от цены вещи
    "protect": 0.0086,          # защита от рисков, доля от цены вещи
    "cn_ship_cny": 0.0,         # доставка по Китаю до склада, ¥
    "intl_per_kg": 884.0,       # международная доставка, ₽ за кг
    "intl_min": 0.0,            # минимальная плата за посылку, ₽
    "ru_ship": 1268.54,         # доставка по РФ, ₽
    "photo": 150.0,             # фотоотчёт и сборы, ₽
    "expert_cny": 50.0,         # экспертиза, ¥
    "duty_free_eur": 200.0,     # беспошлинный лимит на посылку, €
    "duty_rate": 0.15,          # пошлина с превышения
    "other": 0.03,              # прочее/возвраты, доля от цены продажи
    "target": 0.25,             # целевая маржа для «Брать до»
    "alert_min": 3000.0,        # уведомлять о новой вещи (и снижении цены не у ★), если прибыль не меньше, ₽
    "jp_feed_min": 0.0,         # Mercari: в ленту — только если с посредником прибыль не меньше, ₽ (иначе в архив)
    "weights": {"sneakers": 1.5, "shoes": 1.5, "outerwear": 2.0, "clothing": 0.6, "bag": 1.2,
                "accessories": 0.3, "jewelry": 0.2, "?": 1.0},
}
COSTS: dict = json.loads(json.dumps(COST_DEFAULTS))


def load_costs() -> dict:
    try:
        saved = json.loads(COSTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        saved = {}
    COSTS.clear()
    COSTS.update(json.loads(json.dumps(COST_DEFAULTS)))
    for k, v in saved.items():
        if k == "weights" and isinstance(v, dict):
            COSTS["weights"].update(v)
        elif k in COST_DEFAULTS:
            COSTS[k] = v
    if saved and saved.get("jp_profile") != "OZTA":     # был профиль Maketto по умолчанию — один раз ставим OZTA
        COSTS.update({k: COST_DEFAULTS[k] for k in ("jp_profile", "jp_fee", "jp_min_usd", "jp_intl_kg", "jp_ship_fee", "jp_ru_ship")})
        COSTS["rate_date"] = ""    # курс доллара — сегодня же, при первом обновлении курса
    return COSTS


def save_costs():
    COSTS_FILE.write_text(json.dumps(COSTS, ensure_ascii=False, indent=2), encoding="utf-8")


def fetch_cbr_rates() -> tuple[float, float, str, float, float]:
    """Курсы ¥ и € от ЦБ РФ на сегодня: официальный XML, а если cbr.ru недоступен (VPN) — его зеркало cbr-xml-daily.ru,
    а если и оно — рыночный курс open.er-api.com (российские сайты через VPN не открываются)."""
    import urllib.request
    import xml.etree.ElementTree as ET
    try:
        with urllib.request.urlopen("https://www.cbr.ru/scripts/XML_daily.asp", timeout=10) as r:
            root = ET.fromstring(r.read())
        out = {}
        for v in root.iter("Valute"):
            code = v.findtext("CharCode")
            if code in ("CNY", "EUR", "JPY", "USD"):
                out[code] = float(v.findtext("Value").replace(",", ".")) / float(v.findtext("Nominal"))
        return out["CNY"], out["EUR"], "ЦБ", out["JPY"], out["USD"]
    except Exception:
        pass
    try:
        req = urllib.request.Request("https://www.cbr-xml-daily.ru/daily_json.js", headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            v = json.loads(r.read())["Valute"]
        return (v["CNY"]["Value"] / v["CNY"]["Nominal"], v["EUR"]["Value"] / v["EUR"]["Nominal"], "ЦБ",
                v["JPY"]["Value"] / v["JPY"]["Nominal"], v["USD"]["Value"] / v["USD"]["Nominal"])
    except Exception:
        pass
    req = urllib.request.Request("https://open.er-api.com/v6/latest/CNY", headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        v = json.loads(r.read())["rates"]
    return v["RUB"], v["RUB"] / v["EUR"], "рынок", v["RUB"] / v["JPY"], v["RUB"] / v["USD"]


def update_rates(force: bool = False) -> str:
    """Раз в день берёт курс у ЦБ (или рыночный); при ошибке остаётся прошлый. Наценка (курс KwikPay) остаётся той же.
    Возвращает пояснение для вкладки."""
    today = date.today().isoformat()
    if not COSTS.get("rate_auto") or (COSTS.get("rate_date") == today and not force):
        return ""
    try:
        cny, eur, src, jpy, usd = fetch_cbr_rates()
    except Exception as e:
        return f"курс не получен ни от ЦБ, ни с рынка ({type(e).__name__}), считаю по {COSTS['rate']:.4f}"
    COSTS.update(rate=round(cny, 4), eur_rate=round(eur, 4), rate_date=today, rate_src=src, jpy_rate=round(jpy, 5),
                 usd_rate=round(usd, 4))
    save_costs()
    return ""


def eff_rate() -> float:
    return COSTS["rate"] * (1 + COSTS["rate_markup"])


def jpy_to_cny(jpy: float) -> float:
    """Иены Mercari -> юань-эквивалент: в нём лента сравнивает цены и пороги каталога."""
    return round(jpy * COSTS["jpy_rate"] / max(COSTS["rate"], 0.01), 2)


def jp_rate() -> float:
    """₽ за иену с наценкой посредника — по нему считаются затраты японской вещи."""
    return COSTS["jpy_rate"] * (1 + COSTS["jp_markup"])


def jp_min_jpy() -> float:
    """Минимум комиссии и сбора посредника в иенах: $ (OZTA) по курсу, иначе jp_fee_min."""
    c = COSTS
    return c["jp_min_usd"] * c["usd_rate"] / max(c["jpy_rate"], 1e-6) if c.get("jp_min_usd") else c["jp_fee_min"]


def snap_rows(db, good: list[dict], known: set, cat_ver: str) -> int:
    """Вещи, впервые попавшие в ленту, -> таблица snap: бренд, модель, цена, прогноз продажи и прибыли, оценка, риск,
    курс и версия каталога на этот момент. Позже прогноз не переписывается — иначе его не с чем сравнивать."""
    now = datetime.now()
    num = lambda v: v if isinstance(v, (int, float)) and not isinstance(v, bool) else None
    new = []
    for r in good:
        if r["id"] in known:
            continue
        known.add(r["id"])
        first = r.get("first_ts") or ""
        try:
            late = int((now - datetime.fromisoformat(first)).total_seconds() > 6 * 3600)
        except ValueError:
            late = 1
        new.append((r["id"], now.isoformat(timespec="seconds"), first, late, r.get("site", ""), r.get("brand", ""), r.get("model", ""),
                    r.get("category", ""), r.get("ckey", ""), num(r.get("s_cond")), num(r.get("price_cny")), num(r.get("price_jpy")),
                    num(r.get("sell_rub")), num(r.get("profit_rub")), num(r.get("cost_rub")), num(r.get("total")), r.get("tier", ""),
                    r.get("fake_risk", ""), num(r.get("deal_med")), COSTS["rate"], COSTS["jpy_rate"], cat_ver))
    if new:
        db.executemany("INSERT OR IGNORE INTO snap VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", new)
    c = COSTS
    db.execute("INSERT OR REPLACE INTO rates(day, cny, jpy, eur, usd, markup) VALUES(?,?,?,?,?,?)",
               (date.today().isoformat(), c["rate"], c["jpy_rate"], c["eur_rate"], c["usd_rate"], c["rate_markup"]))
    db.commit()
    return len(new)


def basket_calc(rows: list[dict]) -> dict:
    """«Корзина Японии»: несколько вещей Mercari одной посылкой через посредника. Комиссия за выкуп — с каждой вещи
    (каждая покупается отдельно), а доставка, её сбор (минимум $20), пошлина и путь по России — один раз на посылку.
    rows — вещи ленты (price_jpy, kg, profit_rub, cost_rub). Возвращает статьи затрат и сравнение «поодиночке / вместе»."""
    c, jr, mn = COSTS, jp_rate(), jp_min_jpy()
    goods = sum((r.get("price_jpy") or 0) * jr for r in rows)
    fee = sum(max((r.get("price_jpy") or 0) * c["jp_fee"], mn) * jr for r in rows)
    kg = sum(r.get("kg") or c["weights"].get(r.get("ckey") or "?", 1.0) for r in rows)
    t = kg * c["jp_intl_kg"]
    ship, ship_fee = t * jr, (max(t * c["jp_ship_fee"], mn) * jr if rows else 0)
    duty = max(goods / max(c["eur_rate"], 1) - c["duty_free_eur"], 0) * c["duty_rate"] * c["eur_rate"]
    parts = [("вещи", goods), ("комиссия за выкуп (с каждой вещи)", fee), ("доставка по Японии", c["jp_dom"] * jr * len(rows)),
             (f"перевозчик до Владивостока ({kg:g} кг)", ship), ("сбор посредника за доставку (один на посылку)", ship_fee),
             ("пошлина (со всей посылки)", duty), ("Владивосток → Бердск (один раз)", c["jp_ru_ship"] if rows else 0),
             ("прочее за посылку", c["jp_extra"] if rows else 0)]
    cost = sum(v for _, v in parts)
    known = [r for r in rows if isinstance(r.get("profit_rub"), int) and isinstance(r.get("cost_rub"), int)]
    payout = sum(r["profit_rub"] + r["cost_rub"] for r in known)      # что остаётся после продажи до вычета затрат
    alone = sum(r["profit_rub"] for r in known)
    return {"parts": [(n, round(v)) for n, v in parts if round(v)], "cost": round(cost), "kg": round(kg, 2), "n": len(rows),
            "payout": round(payout), "together": round(payout - cost) if len(known) == len(rows) and rows else None,
            "alone": alone if len(known) == len(rows) and rows else None, "unknown": len(rows) - len(known)}


def set_pay_rate(pay: float):
    """Вы вписали курс KwikPay: наценка = он к сегодняшнему курсу ЦБ/рынка. Дальше курс обновляется сам, наценка та же."""
    update_rates()      # курс на сегодня (если ещё не брали) — наценку считаем к нему
    COSTS.update(pay_rate=round(pay, 4), pay_date=date.today().isoformat(),
                 rate_markup=round(min(max(pay / COSTS["rate"] - 1, -0.2), 0.5), 5))


# вес посылки по слову в названии: точнее одного веса на всю категорию (кошелёк и дорожная сумка — обе «сумки»).
# Первое совпадение по порядку; категории без совпадения — вес категории со вкладки «Сделки»
WEIGHT_WORDS = [
    (("bag", "accessories"), 0.3, ["钱包", "卡包", "卡夹", "零钱包", "wallet", "card holder", "cardholder"]),
    (("bag",), 1.6, ["旅行袋", "旅行包", "行李袋", "keepall", "duffle", "duffel"]),
    (("bag",), 1.3, ["双肩", "背包", "backpack"]),
    (("bag",), 0.5, ["腰包", "胸包", "belt bag", "bumbag"]),
    (("bag",), 0.8, ["斜挎", "邮差", "messenger", "单肩", "腋下"]),
    (("outerwear",), 2.2, ["皮衣", "皮夹克", "leather jacket"]),
    (("outerwear",), 1.8, ["羽绒", "down jacket", "puffer"]),
    (("outerwear",), 1.5, ["风衣", "trench"]),
    (("outerwear",), 1.2, ["夹克", "jacket"]),
    (("clothing",), 0.3, ["t恤", "短袖", "t-shirt", "tee", "背心"]),
    (("clothing",), 0.4, ["衬衫", "shirt"]),
    (("clothing",), 0.8, ["卫衣", "hoodie", "毛衣", "针织", "sweater", "knit", "开衫", "牛仔裤", "jeans", "裤", "pants"]),
    (("shoes", "sneakers"), 2.2, ["靴", "boots", "boot"]),
    (("shoes", "sneakers"), 0.9, ["拖鞋", "凉鞋", "slides", "sandal"]),
]
# «重量: 1.2kg», «净重约800g», «重2斤» — вес вещи, если продавец его указал
WEIGHT_RX = re.compile(r"(?:净重|重量|重)\s*[:：]?\s*(?:约|大约|大概|为)?\s*(\d+(?:\.\d+)?)\s*(kg|公斤|千克|斤|g|克)", re.I)
PACK_KG = 0.2       # упаковка сверх веса вещи из объявления


def item_weight(title: str, text: str, ckey: str) -> tuple[float, str]:
    """Вес посылки, кг, и откуда он взят: из объявления (+ упаковка), по слову в названии или по категории."""
    m = WEIGHT_RX.search(text)
    if m:
        v = float(m.group(1))
        kg = v / 1000 if m.group(2).lower() in ("g", "克") else v / 2 if m.group(2) == "斤" else v
        if 0.05 <= kg <= 8:
            return round(kg + PACK_KG, 2), f"указан в объявлении: {m.group(0)}"
    for cats, kg, words in WEIGHT_WORDS:
        if ckey in cats:
            h = hits(title, words)
            if h:
                return kg, f"по слову «{h[0]}»"
    w = COSTS["weights"]
    return w.get(ckey or "?", w.get("?", 1.0)), "по категории"


def cost_parts(price_cny: float, ckey: str, kg: float | None = None, site: str = "") -> list[tuple[str, float]]:
    """Затраты по статьям, ₽ — та же формула считается в карточке (calcPop) и на вкладке «Сделки».
    kg — вес посылки из item_weight(); без него — вес категории. site="mercari" — Япония через посредника
    (price_cny у неё — юань-эквивалент, см. jpy_to_cny)."""
    c, r = COSTS, eff_rate()
    if kg is None:
        kg = c["weights"].get(ckey or "?", c["weights"].get("?", 1.0))
    if site == "mercari":
        jr, mn = jp_rate(), jp_min_jpy()
        jpy = price_cny * c["rate"] / max(c["jpy_rate"], 1e-6)
        goods, t = jpy * jr, kg * c["jp_intl_kg"]
        over_eur = goods / max(c["eur_rate"], 1) - c["duty_free_eur"]
        return [("вещь", goods), ("комиссия за выкуп", max(jpy * c["jp_fee"], mn if jpy else 0) * jr),
                ("доставка по Японии", c["jp_dom"] * jr), (f"перевозчик до Владивостока ({kg:g} кг)", t * jr),
                ("сбор посредника за доставку", max(t * c["jp_ship_fee"], mn if t else 0) * jr),
                ("пошлина", max(over_eur, 0) * c["duty_rate"] * c["eur_rate"]), ("Владивосток → Бердск", c["jp_ru_ship"]),
                ("прочее за посылку", c["jp_extra"])]
    goods = price_cny * r
    over_eur = goods / max(c["eur_rate"], 1) - c["duty_free_eur"]
    return [("вещь", goods), ("комиссия байера", goods * c["buyer_fee"]), ("защита", goods * c["protect"]),
            ("доставка по Китаю", c["cn_ship_cny"] * r), (f"международная доставка ({kg:g} кг)", max(kg * c["intl_per_kg"], c["intl_min"])),
            ("пошлина", max(over_eur, 0) * c["duty_rate"] * c["eur_rate"]), ("доставка по РФ", c["ru_ship"]),
            ("фотоотчёт и сборы", c["photo"]), ("экспертиза", c["expert_cny"] * r)]


def profit_calc(price_cny: float, sell: float, a, cat: dict, ckey: str = "?", kg: float | None = None,
                site: str = "") -> dict:
    """Прибыль по каждому каналу продаж из каталога; возвращает лучший канал."""
    c, r = COSTS, eff_rate()
    cost = sum(v for _, v in cost_parts(price_cny, ckey, kg, site))
    # «Брать до»: затраты = цена × k + постоянные (+ пошлина сверх лимита) — решаем относительно цены
    k = r * (1 + c["buyer_fee"] + c["protect"])
    fixed = sum(v for _, v in cost_parts(0, ckey, kg, site))
    if site == "mercari":       # ₽ за юань-эквивалент: иены × курс посредника
        r = c["rate"] / max(c["jpy_rate"], 1e-6) * jp_rate()
        k = r * (1 + c["jp_fee"])
    eur, lim, d = max(c["eur_rate"], 1), c["duty_free_eur"], c["duty_rate"]

    def solve(budget, k_, fixed_):
        mb = (budget - fixed_) / k_
        if mb * r / eur > lim:
            mb = (budget - fixed_ + lim * d * eur) / (k_ + r * d)
        return mb
    channels = cat.get("channels") or [{"name": "продажа", "fee_rate": a.comm}]
    best = None
    for ch in channels:
        gross = sell * ch.get("price_factor", 1.0)
        payout = gross * (1 - ch.get("fee_rate", 0.0)) - ch.get("fixed_fee_rub", 0) - gross * (a.tax + c["other"])
        profit = payout - cost
        budget = payout * (1 - c["target"])
        max_buy = solve(budget, k, fixed)
        # Mercari: комиссия — большее из 10% и минимума; у недорогой вещи в силе минимум
        if site == "mercari" and max_buy * c["rate"] / max(c["jpy_rate"], 1e-6) * c["jp_fee"] < jp_min_jpy():
            max_buy = solve(budget, r, fixed + jp_min_jpy() * jp_rate())
        if best is None or profit > best["profit_rub"]:
            best = {"best_channel": ch["name"], "profit_rub": round(profit),
                    "max_buy_cny": round(max(max_buy, 0)), "cost_rub": round(cost)}
            if site == "mercari":       # «Брать до» в иенах — так цена стоит на Mercari
                best["max_buy_jpy"] = round(max(max_buy, 0) * c["rate"] / max(c["jpy_rate"], 1e-6), -2)
    roi = best["profit_rub"] / max(best["cost_rub"], 1)
    best["s_profit"] = clamp(40 + 60 * roi)       # окупаемость 0% = 40, 50% = 70, 100% = 100
    return best


# ---------------------------------------------------------------- сделки (учёт покупок)
DEAL_STATUSES = ["куплено", "в пути", "у меня", "продано"]
EXPERT_RESULTS = ["оригинал", "сомнительно", "подделка"]      # журнал экспертиз: результат проверки подлинности купленной вещи
RISK_FLAGS = ("fake_risk", "risk_why", "total", "tier", "deal_pct", "price_vs_median", "sim_photo", "sim_text", "ai_risk", "tags", "site")
OWN_SALES: dict = {}        # ("m"/"c", бренд, модель/тип) -> (медиана продаж ₽, сколько продаж)


# Цена продажи от рынка России: объявления каналов, Avito и Oskelly за 4 месяца (заполняет refresh_market_sell в gp_market).
MARKET_SELL: dict = {}      # ("m"/"c", бренд, модель/тип) -> (цена ₽ уже с поправкой «просят → продают», сколько объявлений)
MKT_PRIOR = 5               # цена каталога весит как 5 объявлений: чем больше объявлений, тем меньше от неё остаётся


def market_sell(brand: str, model: str, ckey: str, base, model_priced: bool = False):
    """(цена ₽, объявлений) по рынку России или None, если объявлений мало. Сначала та же модель (от 3 объявлений),
    потом бренд и тип вещи (от 8). У модели со своей ценой в каталоге бренд-и-тип не берём: там в одной куче
    и кошельки, и дорожные сумки. base — цена каталога, если она есть: рынок смешивается с ней по числу объявлений."""
    g = MARKET_SELL.get(("m", brand, model)) if model else None
    if not g and not model_priced:
        g = MARKET_SELL.get(("c", brand, ckey or "?"))
    if not g:
        return None
    v, n = g
    if base:
        v = (n * v + MKT_PRIOR * base) / (n + MKT_PRIOR)
    return round(v / 500) * 500, n


def own_sale(brand: str, model: str, ckey: str):
    """Цена продажи по вашим продажам: сначала та же модель, потом тот же бренд и тип вещи."""
    return (model and OWN_SALES.get(("m", brand, model))) or OWN_SALES.get(("c", brand, ckey or "?"))


def load_deals(db) -> list[dict]:
    db.execute("""CREATE TABLE IF NOT EXISTS deals(id INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT, data TEXT NOT NULL,
                  status TEXT, ts_buy TEXT, ts_sold TEXT)""")
    out = []
    for did, iid, data, st, tb, ts in db.execute("SELECT id, item_id, data, status, ts_buy, ts_sold FROM deals ORDER BY id DESC"):
        d = json.loads(data)
        d.update(deal_id=did, item_id=iid, status=st, ts_buy=tb, ts_sold=ts or "")
        out.append(d)
    return out


def refresh_own_sales(deals: list[dict]) -> bool:
    """Пересчитывает OWN_SALES; True, если что-то поменялось (тогда ленту надо переоценить)."""
    groups: dict = {}
    for d in deals:
        if d["status"] == "продано" and d.get("sold_rub"):
            if d.get("model"):
                groups.setdefault(("m", d["brand"], d["model"]), []).append(d["sold_rub"])
            groups.setdefault(("c", d["brand"], d.get("ckey") or "?"), []).append(d["sold_rub"])
    new = {k: (round(statistics.median(v) / 500) * 500, len(v)) for k, v in groups.items()}
    if new == OWN_SALES:
        return False
    OWN_SALES.clear()
    OWN_SALES.update(new)
    return True


def deal_numbers(d: dict) -> dict:
    """Затраты/прибыль сделки: по факту, если цифры вписаны, иначе по прогнозу при покупке."""
    cost = d.get("cost_real") or d.get("cost_rub") or 0
    sold = d.get("sold_rub") if d["status"] == "продано" else None
    fee = d.get("sold_fee") or 0
    return {"cost": cost, "profit": (sold - fee - cost) if sold else None, "plan": d.get("profit_rub")}


def total_score(row: dict, cat: dict) -> int:
    """Взвешенное среднее; неизвестные части (нет цены продажи) не тянут оценку вниз."""
    w = cat.get("weights", {})
    parts = {"profit": row.get("s_profit"), "liquidity": row.get("s_liq"), "condition": row.get("s_cond"),
             "desire": row.get("s_desire"), "trust": row.get("s_trust")}
    num = den = 0.0
    for k, v in parts.items():
        if v in ("", None):
            continue
        num += w.get(k, 0) * v
        den += w.get(k, 0)
    return min(100, max(0, round(num / den) - row.get("risk_penalty", 0) + row.get("deal_bonus", 0))) if den else 0


# ---------------------------------------------------------------- логика
def evaluate(row: dict, a, cat: dict, prices: dict) -> tuple[bool, str]:
    title = low(row.get("title", ""))
    # метки выдачи («品牌: YSL/圣罗兰 | 几乎全新») — как часть описания: бренд и состояние видны без открытия карточки
    desc = low((row.get("desc", "") + " 【" + row["labels"] + "】") if row.get("labels") else row.get("desc", ""))
    ai = row.get("ai") or {}
    if ai.get("brand"):      # бренд, который ИИ увидел на фото, важнее поля «品牌» от продавца (бывает неверным)
        desc = "【фото: " + low(ai["brand"]) + "】 " + re.sub(r"品牌[:：]\s*[^|】]+", "", desc)
    text = title + " | " + desc
    if row.get("mark") == "hide":
        return False, "скрыто вами"
    if ai.get("gender") == "women":
        return False, "женское (ИИ по фото)"
    if a.only_new and not row.get("status"):
        return False, "уже было в прошлых запусках"
    if has_any(text, SOLD_WORDS):
        return False, "уже продано (выставлено «посмотреть»)"
    if placeholder_price(row.get("price_cny", 0)):
        return False, "цена-заглушка (не для продажи)"
    bkey, why = detect_brand(title, text, cat)
    if not bkey:
        if a.brands or why.startswith("несколько брендов"):
            return False, why
        return evaluate_outside(row, title, desc, text, a, cat, why)
    brand = cat["brands"][bkey]
    row["brand"] = brand["name"]
    if a.brands and bkey not in a.brands and low(brand["name"]) not in a.brands:
        return False, "бренд не выбран (--brands)"
    if need_check(brand, row):
        return False, f"{brand['name']} без 验货宝 — на Goofish почти всё подделки"
    if ("鉴定" in text or "鉴别" in text) and not has_any(text, SERVICE_OK_PHRASES):
        return False, "услуга экспертизы, а не вещь"
    if "元/" in row.get("tags", ""):
        return False, "услуга (цена за раз/от)"
    nr = has_any(text, NON_RESALE_WORDS)
    if nr:
        return False, "предзаказ/посредник: " + nr[0]

    model = detect_model(text, brand, title)
    ckey = detect_category(title, desc, cat)
    if model and model.get("strict") and ckey not in ("", model.get("category")):
        model = None            # «свитер» Stone Island, а вещь — куртка: цена свитера не подходит
    if model and model.get("category"):
        mk = model["category"]
        if not ckey or not cat["categories"][ckey].get("enabled", True) or CAT_FAMILY.get(ckey) == CAT_FAMILY.get(mk):
            ckey = mk           # кеды/обувь, куртка/одежда — модель уточняет тип
        else:
            model = None        # «Saffiano» на визитнице: это не сумка-модель, и её цена (30 000 ₽) не подходит
    if not ckey and ai.get("category") in cat["categories"]:
        ckey = ai["category"]       # тип вещи по фото
    if not ckey and ((len(row.get("title", "")) <= TRUNC_LEN and not desc) or "品牌:" in desc):
        # название обрезано и описания нет — или бренд указан в поле карточки «品牌», а тип нигде не назван
        ckey = "?"
    if not ckey:
        return False, "тип вещи не распознан"
    c = cat["categories"].get(ckey, UNKNOWN_CAT)
    row["category"] = c["name"]
    if not c.get("enabled", True):
        return False, "не ищем: " + c["name"]
    if a.types and ckey not in a.types:
        return False, "тип не выбран (--types)"
    # пробелы убираем: в рекомендациях заголовок рвётся посреди слова («高 版本非正»)
    bad = has_any(fake_text(text), FAKE_WORDS + brand.get("fakes", []) + a.exclude_word) or style_fake(text, brand["aliases"])
    if bad:
        return False, "слово-маркер: " + bad[0]

    p = row["price_cny"]
    mp = brand.get("min_price", {})
    min_price = a.min_price if a.min_price is not None else (model or {}).get("min_price") or mp.get(ckey, mp.get("*", c.get("min_price", 500)))
    floor = a.low_floor if a.low_floor is not None else c.get("low_floor", 200)
    tier_b = p < min_price
    if tier_b and a.min_price is None and (model or {}).get("min_price"):
        # порог модели ставится там, где дешёвых оригиналов не бывает (Carryall — без винтажа): это не «проверить», а реплика
        return False, f"дешевле ¥{min_price} для {model['name']} — почти наверняка реплика"
    if tier_b:
        if not a.low_tier:
            return False, "цена ниже минимума (подозрительно)"
        if p < floor:
            return False, "цена ниже абсолютного минимума"
    if a.max_price and p > a.max_price:
        return False, "цена выше максимума"
    if a.city and not any(ct in row.get("city", "") for ct in a.city):
        return False, "город не из списка"
    if a.exclude_city and any(ct in row.get("city", "") for ct in a.exclude_city):
        return False, "город исключён"
    if a.must and not all(low(w) in text for w in a.must):
        return False, "нет обязательного слова"
    if row.get("want", 0) < a.min_want:
        return False, "мало хотят"
    if a.free_shipping and not row.get("free_ship"):
        return False, "нет бесплатной доставки"
    if a.require_model and not model:
        return False, "модель не распознана"

    # желанность: модель (или бренд) + категория бренда + подтип + варианты + редкость
    desire = model.get("desire", brand["desire"]) if model else brand["desire"]
    desire += brand.get("category_desire", {}).get(ckey, 0)
    st_hits = [s for s in c.get("subtypes", []) if hits(text, s["words"])]
    subtype = ""
    if st_hits:   # отрицательный подтип (поясная сумка) важнее положительного
        neg = [s for s in st_hits if s["desire"] < 0]
        st = min(neg, key=lambda s: s["desire"]) if neg else max(st_hits, key=lambda s: s["desire"])
        desire += st["desire"]
        subtype = st["name"]
    variants, grail = [], False
    for v in brand.get("variants", []):
        if v.get("category") and v["category"] != ckey:
            continue
        if hits(text, v["words"]):
            desire += v["desire"]
            variants.append(v["name"])
            grail = grail or v.get("grail", False)
    rar_groups, rar_words, rar = [], [], 0
    for g in cat.get("rarity", []):
        h = hits(text, g["words"])
        if h:
            rar_groups.append(g["name"])
            rar_words += h
            rar += g["desire"]
    desire += min(rar, cat.get("rarity_cap", 15))
    mod_penalty = 0
    for g in cat.get("modifiers", []):
        h = hits(text, g["words"])
        if not h or (g.get("unless") and hits(text, g["unless"])):
            continue
        if g.get("reject"):
            return False, f'{g["name"]}: {h[0]}'
        desire += g.get("desire", 0)
        mod_penalty += g.get("penalty", 0)
        variants.append(g["name"])
    if a.require_rarity and not rar_groups and not grail:
        return False, "нет признаков редкости"

    liq = model.get("liquidity", brand["liquidity"]) if model else brand["liquidity"]
    if ckey in brand.get("ambassador", []):
        liq += 8
    liq += min(row.get("want", 0), 20) * 0.75
    size, size_bonus = size_liquidity(text, ckey, brand["name"])
    liq += size_bonus
    row.update(size=size, size_bonus=size_bonus)

    cond, cond_note, defects = condition_score(text)
    s_trust, trust_words, red = trust_score(text, tier_b)
    # новая вещь по цене б/у: оригинал так дёшево почти не продают (порог поднят с ×1.5 — реплики шли по ×1.2–2)
    # «новый сезон» (新款) с артикулом за цену б/у — так выглядят почти все новые реплики
    new_like = cond >= NEW_COND or bool(has_any(text, ["新款", "最新款", "未拆封"]))
    cheap_new = new_like and p < min_price * 2.5
    # строгий режим: совсем новое (全新/吊牌/新款/未拆封) дешевле 3.5 минимумов — отсев.
    # Значок 验货宝 есть почти у половины объявлений, в том числе у реплик: он лишь снижает порог до 2 минимумов
    # Mercari: 验货宝 там нет вовсе, а новых оригиналов (未使用) дёшево больше — мягкий порог, как у вещи с 验货宝
    jp = row.get("site") == "mercari"
    yhb = "验货宝" in row.get("tags", "")
    k_new = 2.0 if yhb or jp else 3.5
    if (cond >= 98 or has_any(text, ["新款", "最新款", "未拆封"])) and p < min_price * k_new:
        return False, ("новое дешевле" if jp else f"новое{' (даже с 验货宝)' if yhb else ' без 验货宝'} дешевле") + f" ¥{min_price * k_new:.0f}"
    if cheap_new:
        s_trust = clamp(s_trust - 15)
        red.append("новое и дёшево")
    if tier_b:
        low_score, signals, red_b = low_price_signals(text)
        if red_b:
            return False, "красный флаг: " + red_b[0]
        arch = has_any(text, LOW_ARCHIVE)
        if arch:
            low_score += 2
            signals.append(arch[0])
        if cond >= NEW_COND:
            low_score -= 2
        if low_score < a.low_min:
            return False, "низкая цена без достаточных сигналов"
        row.update(tier="B", low_signals=ru_words(signals))
        s_trust = min(s_trust, 50)   # дешевле порога: не даём подняться наверх только за счёт выгоды
    else:
        row.update(tier="A", low_signals="")
    if ckey == "?":
        row["tier"] = "B"       # тип не ясен — сначала проверить, ниже распознанных вещей

    sell, cap_k, own = None, None, None
    for k in ([model["name"]] if model else []) + [brand["name"]]:
        if k in prices:
            sell = prices[k]
            break
    if sell is None:
        own = own_sale(brand["name"], model["name"] if model else "", ckey)
        if own:
            sell = own[0]
    fixed, mk, src = sell is not None, None, "own" if own else ""
    if sell is None and model:
        sell, cap_k = model.get("sell_rub"), 4.0
    if sell is None:
        sell, cap_k = brand.get("sell_rub", {}).get(ckey), 3.0
    if not fixed:       # рынок России важнее каталога: каталог остаётся опорой, пока объявлений мало
        mk = market_sell(brand["name"], model["name"] if model else "", ckey, sell, bool(model and model.get("sell_rub")))
        if mk:
            sell, cap_k, src = mk[0], cap_k or 3.0, "market"
    if sell is None and ckey == "?" and brand.get("sell_rub"):
        sell = min(brand["sell_rub"].values())    # тип не ясен: самая скромная цена бренда, чтобы не завышать
    if sell is None:
        sell = cat.get("default_sell", {}).get(ckey)   # средняя цена категории для люкса
        src = "stub" if sell else src
    if sell is None and a.default_sell:
        sell = a.default_sell
    row["sell_src"] = src
    if sell:
        sell, note = realistic_sell(sell, p, cond, a.rate, cap_k, kit_factor(text, ckey))
        if own:
            note = f"по вашим продажам ({own[1]} шт.)"
        elif mk:
            note = f"по рынку России: {mk[1]} объявл." + (f"; {note}" if note else "")
        elif src == "stub":
            note = "заглушка: ни в каталоге, ни на рынке России цены для такой вещи нет" + (f"; {note}" if note else "")
        kg, kg_src = item_weight(title, text, ckey)
        row.update(profit_calc(p, sell, a, cat, ckey, kg, row.get("site", "")), sell_rub=sell, sell_note=note, ckey=ckey,
                   kg=kg, kg_src=kg_src)
        if a.only_profitable and row["profit_rub"] <= 0:
            return False, "убыточно по расчёту"
    else:
        row.update(sell_rub="", best_channel="", profit_rub="", max_buy_cny="", cost_rub="", s_profit="", ckey=ckey,
                   kg="", kg_src="")

    row["checks"] = " | ".join((model or {}).get("checks", []) + brand.get("checks", []) + c.get("checks", []))
    row.update(model=model["name"] if model else "", variant=", ".join(variants), subtype=subtype,
               grail="да" if grail else "", s_liq=clamp(liq), s_cond=cond, s_desire=clamp(desire),
               s_trust=s_trust, condition=cond_ru(cond_note), defects=ru_words(defects),
               trust=ru_words(trust_words), red_flags=ru_words(red),
               rarity=" / ".join(rar_groups) + (" [" + ",".join(rar_words) + "]" if rar_words else ""))
    # риск важнее, чем его 10% веса: дешёвая вещь не должна обгонять нормальную только за счёт выгоды
    row["risk_penalty"] = (10 if tier_b else 0) + (5 if cheap_new else 0) + mod_penalty
    row["total"] = total_score(row, cat)
    return True, ""


# ---------------------------------------------------------------- вне каталога
MADE_IN = ["美国制造", "意大利制造", "日本制造", "法国制造", "made in usa", "made in italy", "made in japan",
           "made in france", "made in england"]
REWORK_WORDS = ["改小", "改造", "改款", "大师改", "二次改", "拼接改"]
MEN_WORDS = ["男款", "男士", "男包", "男装", "男鞋", "男生"]
OUTSIDE_CHECKS = ["Найти модель по артикулу/названию и сверить бирки, шрифт логотипа и фурнитуру с оригиналом",
                  "Попросить фото чека, бирок с серийным номером и внутренних швов",
                  "Если есть 验货宝 — покупать через него"]


def need_check(b: dict, row: dict) -> bool:
    """Бренд с пометкой need_check (каталог): на Goofish берём только вещи с 验货宝 (проверка подлинности площадкой).
    Mercari не касается."""
    return bool(b.get("need_check")) and row.get("site") != "mercari" and "验货宝" not in str(row.get("tags", ""))


def outside_brand(text: str, cat: dict) -> tuple | None:
    """Первый названный бренд из списков «интересные»/«шлак»: (позиция, 0=шлак|1=интересный, слово|индекс)."""
    o = cat.get("outside") or {}
    found = []
    for i, b in enumerate(o.get("interesting", [])):
        ps = [p for p in (find_pos(text, w) for w in b["words"]) if p >= 0]
        if ps:
            found.append((min(ps), 1, i))
    for w in o.get("junk", []):
        p = find_pos(text, w)
        if p >= 0:
            found.append((p, 0, w))     # при равной позиции шлак важнее (coach outlet ≠ coach)
    return min(found, key=lambda f: (f[0], f[1])) if found else None


def evaluate_outside(row: dict, title: str, desc: str, text: str, a, cat: dict, why0: str) -> tuple[bool, str]:
    """Бренд не из каталога: интересный бренд или стоящая вещь без бренда попадает в блок «Вне каталога»
    с пометкой «интересно / под вопросом»; шлак и мусор отсеиваются."""
    o = cat.get("outside") or {}
    if not o.get("enabled", True) or not o:
        return False, why0
    hit = outside_brand(text, cat)
    if hit and hit[1] == 0 and not has_any(text, cat.get("collab_words", [])):
        return False, "шлак: " + hit[2]
    b = o["interesting"][hit[2]] if hit and hit[1] == 1 else None
    if b is None and why0 != "бренд не из каталога":   # назван бренд не из списков (или каталожный + чужой)
        return False, why0
    if b and need_check(b, row):
        return False, f"{b['name']} без 验货宝 — на Goofish почти всё подделки"
    if ("鉴定" in text or "鉴别" in text) and not has_any(text, SERVICE_OK_PHRASES):
        return False, "услуга экспертизы, а не вещь"
    if "元/" in row.get("tags", ""):
        return False, "услуга (цена за раз/от)"
    arch, made = has_any(text, LOW_ARCHIVE), has_any(text, MADE_IN)
    nr = has_any(text, NON_RESALE_WORDS)
    if nr and not (nr == ["代购"] and (arch or made)):   # «日本代购» у винтажа — продавец возит из Японии, не предзаказ
        return False, "предзаказ/посредник: " + nr[0]
    ckey = detect_category(title, desc, cat)
    ai = row.get("ai") or {}
    if not ckey and ai.get("category") in cat["categories"]:
        ckey = ai["category"]
    if not ckey and b and ((len(row.get("title", "")) <= TRUNC_LEN and not desc) or "品牌:" in desc):
        ckey = "?"      # обрезанное название у интересного бренда: показываем, уточнится по карточке
    if not ckey or not cat["categories"].get(ckey, UNKNOWN_CAT).get("enabled", True):
        return False, why0 if b is None else "вне каталога: не та категория"
    c = cat["categories"].get(ckey, UNKNOWN_CAT)
    bad = has_any(fake_text(text), FAKE_WORDS + a.exclude_word) or (style_fake(text, b["words"]) if b else [])
    if bad:
        return False, "слово-маркер: " + bad[0]
    p = row["price_cny"]
    min_p = o.get("min_price", 500) if b else o.get("unknown_min_price", 800)
    if p < min_p:
        return False, why0 if b is None else f"вне каталога: дешевле ¥{min_p}"
    red = has_any(text, LOW_RED)
    if red:
        return False, "красный флаг: " + red[0]

    desire, mod_penalty, variants = (b["desire"] if b else 45), 0, []
    for g in cat.get("modifiers", []):
        h = hits(text, g["words"])
        if not h or (g.get("unless") and hits(text, g["unless"])):
            continue
        if g.get("reject"):
            return False, f'{g["name"]}: {h[0]}'
        desire += g.get("desire", 0)
        mod_penalty += g.get("penalty", 0)
        variants.append(g["name"])
    rar_groups, rar = [], 0
    for g in cat.get("rarity", []):
        if hits(text, g["words"]):
            rar_groups.append(g["name"])
            rar += g["desire"]
    desire += min(rar, cat.get("rarity_cap", 15))

    cond, cond_note, defects = condition_score(text)
    s_trust, trust_words, red = trust_score(text, False)
    proof ="验货宝" in row.get("tags", "") or bool(has_any(text, LOW_STRONG))
    pros, cons = [], []
    if arch:
        pros.append("архив/винтаж")
        desire += 8
    if made:
        pros.append("сделано: " + ru_words(made[:1]))
    if proof:
        pros.append("验货宝" if "验货宝" in row.get("tags", "") else "чек/проверка")
    if has_any(text, MEN_WORDS):
        pros.append("мужское")
    pros += [g for g in rar_groups if g != "Архив/винтаж"][:1]
    rework = has_any(text, REWORK_WORDS)
    cheap_new = cond >= NEW_COND and p < min_p * 2.5
    k_new = 2.0 if "验货宝" in row.get("tags", "") or row.get("site") == "mercari" else 3.5
    if (cond >= 98 or has_any(text, ["新款", "最新款", "未拆封"])) and p < min_p * k_new:
        return False, f"вне каталога: новое дешевле ¥{min_p * k_new:.0f}"
    if cheap_new:
        s_trust = clamp(s_trust - 15)
        red.append("новое и дёшево")
        cons.append("новое и дёшево")
    if defects:
        cons.append("дефекты: " + ru_words(defects))
    if b and b.get("need") and not has_any(text, b["need"]):
        return False, "вне каталога: не интересно — " + b.get("need_note", "не та линия")
    if rework or len(defects) >= 2:
        return False, "вне каталога: не интересно — " + ("переделка: " + ru_words(rework[:1]) if rework else "много дефектов")
    # без бренда — только настоящий винтаж/«сделано в…» и при этом чек, 验货宝 или признак редкости
    if b is None and not ((has_any(text, ["中古", "vintage", "古着", "停产"]) or made) and (proof or rar_groups)):
        return False, why0
    verdict = "интересно" if b and not cons else "под вопросом"

    size, size_bonus = size_liquidity(text, ckey)
    if a.require_rarity and not rar_groups:
        return False, "нет признаков редкости"
    if a.types and ckey not in a.types:
        return False, "тип не выбран (--types)"
    liq = 45 + min(row.get("want", 0), 20) * 0.75 + size_bonus
    row.update(size=size, size_bonus=size_bonus, brand=b["name"] if b else "без бренда", category=c["name"], tier="B" if ckey == "?" else "A", low_signals="",
               sell_rub="", best_channel="", profit_rub="", max_buy_cny="", cost_rub="", s_profit="",
               checks=" | ".join((b or {}).get("checks", []) + c.get("checks", []) + OUTSIDE_CHECKS[:1]),
               model="", variant=", ".join(variants), subtype="", grail="", s_liq=clamp(liq), s_cond=cond,
               s_desire=clamp(desire), s_trust=s_trust, condition=cond_ru(cond_note), defects=ru_words(defects),
               trust=ru_words(trust_words), red_flags=ru_words(red), rarity=" / ".join(rar_groups),
               outside=verdict, outside_brand=b["name"] if b else "",
               verdict_why="; ".join(pros + cons) or ("бренд из списка интересных" if b else ""))
    bs = (b or {}).get("sell_rub", {})
    own = own_sale(b["name"], "", ckey) if b else None
    mk = None if own or not b else market_sell(b["name"], "", ckey, bs.get(ckey))
    sell = (own[0] if own else None) or (mk[0] if mk else None) or bs.get(ckey) or (min(bs.values()) if ckey == "?" and bs else None)
    src = "own" if own else "market" if mk else ""
    if not sell:
        sell = cat.get("default_sell", {}).get(ckey)
        src = "stub" if sell else ""
    row.update(ckey=ckey, sell_src=src)
    if sell:
        sell, note = realistic_sell(sell, p, cond, a.rate, None if own else 4.0 if bs.get(ckey) else 3.0, kit_factor(text, ckey))
        if own:
            note = f"по вашим продажам ({own[1]} шт.)"
        elif mk:
            note = f"по рынку России: {mk[1]} объявл." + (f"; {note}" if note else "")
        elif src == "stub":
            note = "заглушка: ни в каталоге, ни на рынке России цены для такой вещи нет" + (f"; {note}" if note else "")
        kg, kg_src = item_weight(title, text, ckey)
        row.update(profit_calc(p, sell, a, cat, ckey, kg, row.get("site", "")), sell_rub=sell, sell_note=note, kg=kg,
                   kg_src=kg_src)
        if a.only_profitable and row["profit_rub"] <= 0:
            return False, "убыточно по расчёту"
    row["risk_penalty"] = (5 if cheap_new else 0) + mod_penalty
    row["total"] = total_score(row, cat)
    return True, ""


_EVAL_CACHE: dict = {}


def evaluate_cached(row: dict, a, cat: dict, prices: dict) -> tuple[bool, str]:
    """evaluate() с памятью: в режиме --listen база пересчитывается после каждой страницы."""
    key = tuple(str(row.get(k, "")) for k in
                ("id", "title", "desc", "labels", "price_cny", "want", "tags", "mark", "status", "city", "free_ship",
                 "ai"))
    hit = _EVAL_CACHE.get(key)
    if hit is None:
        before = set(row)
        ok, why = evaluate(row, a, cat, prices)
        hit = _EVAL_CACHE[key] = (ok, why, {k: row[k] for k in row if k not in before})
    else:
        row.update(hit[2])
    return hit[0], hit[1]


# ---------------------------------------------------------------- база
ROW_DEFAULTS = {"title": "", "price_cny": 0.0, "city": "", "seller": "", "seller_id": "", "want": 0, "free_ship": False,
                "image": "", "url": "", "desc": "", "labels": "", "queries": "", "tags": "", "posted": "", "drop": ""}


def open_db(path: Path, outdir: Path, reset: bool):
    import sqlite3
    if reset and path.exists():
        bak = path.with_name(f"{path.name}.bak-{int(time.time())}")
        path.rename(bak)
        say(f"База начата заново, старая сохранена: {bak}")
    fresh = not path.exists()
    db = sqlite3.connect(path, check_same_thread=False)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS items(id TEXT PRIMARY KEY, data TEXT NOT NULL, first_seen TEXT,
            last_seen TEXT, price REAL, prev_price REAL, price_changed TEXT, mark TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS history(id TEXT, ts TEXT, price REAL);
        CREATE TABLE IF NOT EXISTS marks_log(id TEXT, mark TEXT, reason TEXT, ts TEXT);
        CREATE TABLE IF NOT EXISTS learn_dismissed(key TEXT PRIMARY KEY, ts TEXT);
        CREATE TABLE IF NOT EXISTS market(key TEXT PRIMARY KEY, source TEXT, url TEXT, ts TEXT, seen TEXT, text TEXT);
        CREATE TABLE IF NOT EXISTS phash(id TEXT PRIMARY KEY, h TEXT, ts TEXT);
        -- продавцы из открытых вами карточек: числовой id (ссылка на профиль), шифрованный id из поиска, отзывы
        CREATE TABLE IF NOT EXISTS sellers(uid TEXT PRIMARY KEY, enc TEXT, nick TEXT, data TEXT, ts TEXT);
    """)
    # архив: вещь остаётся в базе (история цен, «уже видели», счётчики), но не участвует в пересчёте ленты.
    # arch: 0 — в работе; 1 — отсеяна (мусор, убыток); 2 — была в ленте, давно не попадалась; 3 — отсеяна и давно не попадалась
    if "arch" not in {c[1] for c in db.execute("PRAGMA table_info(items)")}:
        db.execute("ALTER TABLE items ADD COLUMN arch INTEGER DEFAULT 0")
    db.executescript("""
        CREATE INDEX IF NOT EXISTS items_arch ON items(arch);
        CREATE INDEX IF NOT EXISTS items_first ON items(first_seen);
        -- проданные лоты Mercari: цена настоящей сделки (иены) и за сколько дней ушло
        CREATE TABLE IF NOT EXISTS sold(id TEXT PRIMARY KEY, site TEXT, title TEXT, brand TEXT, model TEXT, category TEXT,
            cond TEXT, price REAL, posted TEXT, seen TEXT, days REAL);
        CREATE INDEX IF NOT EXISTS sold_brand ON sold(brand);
        -- что Щебень думал о вещи, когда она впервые попала в ленту (для бэктеста: прогноз против исхода).
        -- late = 1 — вещь была в базе раньше, чем появилась эта таблица: запись сделана задним числом
        CREATE TABLE IF NOT EXISTS snap(id TEXT PRIMARY KEY, ts TEXT, first_ts TEXT, late INTEGER, site TEXT, brand TEXT, model TEXT,
            category TEXT, ckey TEXT, cond REAL, price REAL, price_jpy REAL, sell_rub REAL, profit_rub REAL, cost_rub REAL,
            total REAL, tier TEXT, fake_risk TEXT, deal_med REAL, rate REAL, jpy_rate REAL, cat_ver TEXT);
        -- курсы по дням: затраты прошлых дней бэктест считает по курсу того дня
        CREATE TABLE IF NOT EXISTS rates(day TEXT PRIMARY KEY, cny REAL, jpy REAL, eur REAL, usd REAL, markup REAL);
        -- «толк» запросов по вещам, ушедшим в архив: сколько видели, сколько из них было в ленте, лучшая прибыль
        CREATE TABLE IF NOT EXISTS qarch(key TEXT PRIMARY KEY, seen INTEGER DEFAULT 0, good INTEGER DEFAULT 0, best INTEGER DEFAULT 0);
    """)
    db.commit()
    PHASH.update({i: int(h, 16) for i, h in db.execute("SELECT id, h FROM phash")})
    if fresh and not reset:
        migrate_old(db, outdir)
    return db


def migrate_old(db, outdir: Path):
    """Переносит в новую базу память прошлых запусков (seen.json) и последние CSV."""
    seen = {}
    if (outdir / "seen.json").exists():
        try:
            seen = json.loads((outdir / "seen.json").read_text(encoding="utf-8"))
        except Exception:
            seen = {}
    rows = {iid: dict(ROW_DEFAULTS, id=iid, title=s.get("title", ""), price_cny=s.get("price") or 0.0)
            for iid, s in seen.items()}
    for name in ("all_items.csv", "best.csv"):
        if not (outdir / name).exists():
            continue
        with open(outdir / name, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                iid = r.get("id")
                if not iid:
                    continue
                row = rows.setdefault(iid, dict(ROW_DEFAULTS, id=iid))
                for k in ("title", "city", "seller", "tags", "posted", "drop", "url", "queries", "image", "desc"):
                    if r.get(k):
                        row[k] = r[k]
                if r.get("price_cny"):
                    row["price_cny"] = to_float(r["price_cny"])
                if r.get("want"):
                    row["want"] = int(to_float(r["want"]))
                if r.get("free_ship"):
                    row["free_ship"] = r["free_ship"] == "True"
    today = date.today().isoformat()
    for iid, row in rows.items():
        first = seen.get(iid, {}).get("first_seen") or today
        last = seen.get(iid, {}).get("last_seen") or first
        db.execute("INSERT OR IGNORE INTO items(id, data, first_seen, last_seen, price) VALUES(?,?,?,?,?)",
                   (iid, json.dumps(row, ensure_ascii=False), first, last, row["price_cny"]))
    db.commit()
    if rows:
        say(f"Создана база {outdir / 'items.db'}: перенесено объявлений из seen.json/CSV: {len(rows)}")


def skey(q: str, site: str = "") -> str:
    """Ключ запроса в «толке» (qstats) и «новое за час»: у Mercari свой — «undercover» на Goofish и на Mercari разные поиски."""
    return "jp:" + q if site == "mercari" else q


# --- фильтр на входе: явный мусор не попадает ни в пересчёт, ни в ленту (сразу в архив, см. ingest)
BOX_ONLY = ["空盒", "空箱", "仅盒", "只有盒", "只出盒", "单盒", "箱のみ", "箱だけ", "袋のみ", "ショッパーのみ", "box only", "empty box"]
PRE_LOW = 0.15          # цена ниже 15% обычной для бренда и типа вещи — не удача, а мусор или подделка
PRE_MED: dict = {}      # (бренд, тип вещи, площадка) -> обычная цена в ленте, ¥ (юань-эквивалент); обновляет пересчёт ленты


def premod(row: dict, cat: dict, exclude=()) -> str:
    """Причина, по которой новое объявление — мусор: стоп-слово реплик, одна упаковка (коробка, пакет), цена ниже 15% обычной.
    "" — объявление идёт в обычную оценку. Те же слова и нижнюю границу цены оценка отсеяла бы и сама — здесь
    это делается до записи в работу, чтобы мусор не занимал пересчёт."""
    title = low(row.get("title", ""))
    labels = low(row.get("labels", ""))
    desc = low(row.get("desc", "")) + " " + labels
    text = title + " | " + desc
    o = cat.get("outside") or {}
    bad = has_any(fake_text(text), FAKE_WORDS + list(exclude) + list(o.get("stop") or []))
    if bad:
        return "стоп-слово: " + bad[0]
    jp = row.get("site") == "mercari"
    box = has_any(title, BOX_ONLY)
    if box:
        return "только упаковка: " + box[0]
    # «с коробкой и пыльником» пишут и у настоящих вещей — поэтому только явное «одна коробка», без догадок
    ckey = detect_category(title, desc, cat)
    bkey, _ = detect_brand(title, text, cat)
    # украшения и аксессуары не судим по цене: булавка и браслет одного бренда отличаются в десятки раз
    if bkey and ckey and ckey not in ("accessories", "jewelry"):
        c = cat["categories"].get(ckey, {})
        p = jpy_to_cny(row.get("price_cny") or 0) if jp else (row.get("price_cny") or 0)
        med = PRE_MED.get((cat["brands"][bkey]["name"], c.get("name", ""), "mercari" if jp else ""))
        if med and p < PRE_LOW * med:
            return f"цена ниже 15% обычной: ¥{p:.0f} при обычной ¥{med:.0f}"
        if not med and p < c.get("low_floor", 200):
            return "цена ниже абсолютного минимума"
    return ""


ARCH_DAYS = {"": 14, "mercari": 3}      # сколько дней вещь остаётся в ленте после последнего показа в поиске
ARCH_BAD_DAYS = 3                       # отсеянная вещь Goofish (её ещё видят счётчики и поиск продавцов реплик)


def qarch_add(db, row: dict, sign: int, was_good: bool, best: int = 0):
    """Вещь уходит в архив (sign=1) или возвращается (−1): её вклад в «толк» запросов переносим в таблицу qarch."""
    for q in str(row.get("queries", "")).split("; "):
        if q:
            db.execute("INSERT INTO qarch(key, seen, good, best) VALUES(?,?,?,?) ON CONFLICT(key) DO UPDATE SET "
                       "seen = MAX(seen + excluded.seen, 0), good = MAX(good + excluded.good, 0), best = MAX(best, excluded.best)",
                       (skey(q, row.get("site", "")), sign, sign if was_good else 0, max(int(best or 0), 0) if sign > 0 else 0))


def unarchive(db, where: str, params: tuple = ()) -> int:
    """Вернуть вещи из архива в работу (следующий пересчёт оценит их заново). where — условие SQL по таблице items."""
    rows = db.execute(f"SELECT data, arch FROM items WHERE arch > 0 AND ({where})", params).fetchall()
    for data, arch in rows:
        qarch_add(db, json.loads(data), -1, arch == 2)
    if rows:
        db.execute(f"UPDATE items SET arch = 0 WHERE arch > 0 AND ({where})", params)
        db.commit()
    return len(rows)


def unarchive_recent(db) -> int:
    """Каталог или ставки изменились: отсеянные за последние дни вещи могли стать годными — вернуть на переоценку.
    (Смена курса раз в день их не возвращает: полпроцента курса вердикт почти не меняют, а пересчёт — десятки секунд.)"""
    return unarchive(db, "arch = 1 AND last_seen >= ?", ((datetime.now() - timedelta(days=ARCH_BAD_DAYS)).isoformat(),))


def ingest(db, items: dict, now: str):
    """Добавляет новые объявления, обновляет известные и запоминает изменение цены."""
    for iid, row in items.items():
        cur = db.execute("SELECT data, price, arch FROM items WHERE id=?", (iid,)).fetchone()
        if cur is None:
            pre = 1 if row.get("pre") else 0      # мусор на входе (premod) — сразу в архив, но «уже видели» помнит
            db.execute("INSERT INTO items(id, data, first_seen, last_seen, price, arch) VALUES(?,?,?,?,?,?)",
                       (iid, json.dumps(row, ensure_ascii=False), now, now, row["price_cny"], pre))
            if pre:
                qarch_add(db, row, 1, False)
            db.execute("INSERT INTO history VALUES(?,?,?)", (iid, now, row["price_cny"]))
            continue
        old = json.loads(cur[0])
        for k, v in row.items():
            if k == "queries":
                qs = [q for q in (old.get("queries", "") + "; " + v).split("; ") if q]
                old["queries"] = "; ".join(dict.fromkeys(qs))
            elif v not in ("", None, 0, False) or k not in old:
                old[k] = v
        price = row["price_cny"]
        moved = bool(price and cur[1] and abs(price - cur[1]) > 0.009)
        if moved:
            db.execute("UPDATE items SET prev_price=?, price_changed=?, price=? WHERE id=?", (cur[1], now, price, iid))
            db.execute("INSERT INTO history VALUES(?,?,?)", (iid, now, price))
        # из архива: снова попалась в поиске (была «давно не видели») или сменила цену (отсеянная могла стать выгодной)
        if cur[2] in (2, 3) or (cur[2] == 1 and moved):
            qarch_add(db, old, -1, cur[2] == 2)
            db.execute("UPDATE items SET arch = 0 WHERE id=?", (iid,))
        db.execute("UPDATE items SET data=?, last_seen=? WHERE id=?", (json.dumps(old, ensure_ascii=False), now, iid))
    db.commit()


def update_fields(db, iid: str, **fields) -> bool:
    cur = db.execute("SELECT data FROM items WHERE id=?", (iid,)).fetchone()
    if cur is None:
        return False
    data = json.loads(cur[0])
    unarchive(db, "id = ?", (iid,))     # карточку открыли или её досмотрел ИИ — вещь снова оценивается (панель покажет почему)
    data.update(fields)
    db.execute("UPDATE items SET data=? WHERE id=?", (json.dumps(data, ensure_ascii=False), iid))
    db.commit()
    return True


JUNK_REASON = "бренд — шлак"
PRICE_REASON, SIZE_REASON = "не оправдывает цену", "неликвидный размер"
HIDE_REASONS = ("женское", "подделка", "не мой стиль", PRICE_REASON, SIZE_REASON, "состояние", "не тот цвет", "не та вещь",
                JUNK_REASON)
# причины про одну конкретную вещь: похожие из-за них не скрываются и «Обучение» по ним слов не ищет
ITEM_ONLY_REASONS = ("состояние", "не тот цвет", "не та вещь")
RULE_MIN = 2        # столько скрытий с одной причиной по одной модели (или размеру) — и похожие отсеиваются сами
LEARNED: dict = {"rules": [], "off": set()}     # правила из скрытий; «off» — те, что вы велели забыть


def size_family(r: dict) -> str:
    return "обувь" if r.get("ckey") in SHOE_CATS else "одежда" if r.get("ckey") in WEAR_CATS else ""


def learned_rules(rows: list[dict], a, cat: dict, prices: dict) -> list[dict]:
    """Правила из ваших скрытий «не оправдывает цену» (по модели, без модели — бренд + тип вещи) и
    «неликвидный размер» (обувь/одежда + размер). ★ сильнее: ★ дороже порога поднимает порог над собой,
    ★ с этим размером снимает правило размера."""
    price: dict[tuple, dict] = {}
    size: dict[tuple, dict] = {}
    for r in rows:
        mk, why = r.get("mark"), r.get("mark_reason", "")
        if mk != "like" and not (mk == "hide" and why in (PRICE_REASON, SIZE_REASON)):
            continue
        x = dict(r, mark="")
        # скрытые иначе не оцениваются: модель и размер узнаём так. ★ уже оценена — её копию без памяти: в памяти
        # осталась бы запись без бренда и модели (они уже были в строке), и снятая ★ потом роняла пересчёт ленты
        ok, _ = (evaluate if mk == "like" else evaluate_cached)(x, a, cat, prices)
        if not ok or not x.get("brand") or not x.get("category"):
            continue
        side = "like" if mk == "like" else "hide"
        if mk == "like" or why == PRICE_REASON:
            price.setdefault((x["brand"], x.get("model") or x["category"]), {"hide": [], "like": []})[side].append(x["price_cny"])
        fam = size_family(x)
        if fam and x.get("size") and (mk == "like" or why == SIZE_REASON):
            size.setdefault((fam, x["size"]), {"hide": [], "like": []})[side].append(x["price_cny"])
    rules = []
    for (b, what), v in sorted(price.items()):
        if len(v["hide"]) < RULE_MIN:
            continue
        thr = min(v["hide"])
        above = [p for p in v["like"] if p >= thr]
        if above:
            thr = max(above) + 1
        rules.append({"key": f"rule:price:{b}|{what}", "kind": "price", "brand": b, "what": what, "thr": thr,
                      "n": len(v["hide"]), "text": f"{b} · {what}: от ¥{thr:,.0f}".replace(",", " ") + f" — «{PRICE_REASON}»",
                      "note": f"скрыто {len(v['hide'])}" + (f", ★ дороже порога: {len(above)}" if above else "")})
    for (fam, sz), v in sorted(size.items()):
        if len(v["hide"]) < RULE_MIN or v["like"]:
            continue
        rules.append({"key": f"rule:size:{fam}|{sz}", "kind": "size", "fam": fam, "size": sz, "n": len(v["hide"]),
                      "text": f"{fam}, размер {sz} — «{SIZE_REASON}»", "note": f"скрыто {len(v['hide'])}"})
    return rules


def rule_hit(r: dict, rules: list[dict]) -> str:
    for x in rules:
        if (x["kind"] == "price" and r.get("brand") == x["brand"] and (r.get("model") or r.get("category")) == x["what"]
                and r["price_cny"] >= x["thr"]):
            return f"{PRICE_REASON}: {x['brand']} {x['what']} от ¥{x['thr']:.0f} вы скрывали"
        if x["kind"] == "size" and r.get("size") == x["size"] and size_family(r) == x["fam"]:
            return f"{SIZE_REASON}: {x['size']} ({x['fam']}, вы скрывали)"
    return ""


def set_mark(db, iid: str, mark: str, reason: str = ""):
    db.execute("UPDATE items SET mark=? WHERE id=?", (mark, iid))
    db.execute("INSERT INTO marks_log VALUES(?,?,?,?)", (iid, mark, reason, datetime.now().isoformat(timespec="seconds")))
    db.commit()


_RELIST_SEEN: dict = {}


def mark_relists(rows: list[dict]):
    """Перевыставление: тот же продавец, почти то же название, новый номер объявления, а старое после этого
    больше не попадалось. Новому ставим relist_price = цена старого (а если стало дешевле — это повод купить)."""
    from difflib import SequenceMatcher
    norm = lambda s: re.sub(r"[\W_]+", "", low(s))[:80]

    def alike(a: str, b: str) -> bool:     # пары названий запоминаем: при пересчёте ленты они те же
        k = (a, b)
        if k not in _RELIST_SEEN:
            m = SequenceMatcher(None, a, b)
            _RELIST_SEEN[k] = m.real_quick_ratio() >= 0.85 and m.quick_ratio() >= 0.85 and m.ratio() >= 0.85
        return _RELIST_SEEN[k]
    by: dict = {}
    for r in rows:
        r["relist_price"] = ""
        k = r.get("seller_id") or r.get("seller")
        if k and not str(k).startswith("продавец ") and len(norm(r.get("title", ""))) >= 6:
            by.setdefault(k, []).append(r)
    for rs in by.values():
        if len(rs) < 2 or len(rs) > 60:     # у магазинов десятки похожих названий — там это не перевыставления
            continue
        rs.sort(key=lambda r: r.get("first_seen", ""))
        ts = [norm(r["title"]) for r in rs]
        for i, r in enumerate(rs):
            for j in range(i - 1, -1, -1):
                old = rs[j]
                if (old["id"] != r["id"] and old.get("last_seen", "") <= r.get("first_seen", "")
                        and old.get("first_seen", "") < r.get("first_seen", "")
                        and alike(ts[i], ts[j])):
                    r["relist_price"] = old.get("price_cny") or ""
                    break


def load_rows(db, session_start: str) -> list[dict]:
    reasons = {iid: r or "" for iid, r in db.execute("SELECT id, reason FROM marks_log WHERE mark='hide' ORDER BY ts")}
    LEARNED["off"] = {k for (k,) in db.execute("SELECT key FROM learn_dismissed WHERE key LIKE 'rule:%'")}
    try:
        import ai_check
        ai_check.init_db(db)
        ai_res = ai_check.results(db)
    except Exception:
        ai_res = {}
    rows = []
    for data, first, last, price, prev, changed, mark in db.execute(
            "SELECT data, first_seen, last_seen, price, prev_price, price_changed, mark FROM items WHERE arch = 0"):
        r = dict(ROW_DEFAULTS, **json.loads(data))
        status = ""
        if (first or "") >= session_start:
            status = "новое"
        elif prev and price and price < prev and (changed or "") >= session_start:
            status = "цена снижена"
        r.update(first_seen=(first or "")[:10], last_seen=(last or "")[:10], first_ts=first or "", mark=mark or "", status=status,
                 mark_reason=reasons.get(r.get("id"), "") if mark == "hide" else "", ai=ai_res.get(r.get("id")),
                 prev_price=prev if prev and price and price < prev else "")
        if r.get("site") == "mercari":      # в базе иены — в ленте юань-эквивалент по сегодняшним курсам
            r["price_jpy"] = price or r.get("price_jpy") or 0
            r["price_cny"] = jpy_to_cny(r["price_jpy"])
            if r["prev_price"]:
                r["prev_price"] = jpy_to_cny(r["prev_price"])
        rows.append(r)
    mark_relists(rows)
    return rows

SELLER_BLACKLIST: dict[str, dict] = {}     # последний расчёт: для вкладки «Статус»
PHASH: dict[str, int] = {}
FIELDS = ["status", "first_seen", "prev_price", "total", "tier", "grail", "s_profit", "s_liq", "s_cond",
          "s_desire", "s_trust", "category", "brand", "model", "variant", "subtype", "title", "price_cny",
          "price_vs_median", "max_buy_cny", "sell_rub", "best_channel", "profit_rub", "rarity", "low_signals",
          "red_flags", "fake_risk", "risk_why", "outside", "verdict_why", "trust", "defects", "condition", "city", "seller", "want", "free_ship", "tags", "posted",
          "drop", "mark", "desc", "queries", "id", "url", "image"]
def write_csv(path: Path, rows: list[dict], fields=None):
    fields = fields or FIELDS
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


__all__ = [n for n in dir() if not n.startswith("__")]   # с «_именами»: модули ниже берут их через *
