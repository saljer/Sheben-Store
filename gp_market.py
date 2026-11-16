"""Сбор через браузер (старый режим), рынок РФ из TG-каналов, сохранённые поиски, заказы клиентов, настройки и Tailscale."""
from __future__ import annotations

from gp_score import *  # noqa: F401,F403


# ---------------------------------------------------------------- браузер
async def collect(a, queries: list[str], outdir: Path) -> dict:
    from playwright.async_api import async_playwright

    items: dict[str, dict] = {}
    state = {"q": "", "hits": 0, "dbg": 0, "calls": 0, "stop": "", "site_block": False}
    interactive = not a.headless

    async def ask(prompt: str) -> str:
        return (await asyncio.to_thread(input, prompt)).strip().lower()

    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            a.profile, headless=a.headless, locale="zh-CN",
            channel="chrome" if a.chrome else None,
            chromium_sandbox=not a.disable_sandbox,
            viewport={"width": 1366, "height": 900},
            args=["--start-minimized"] if a.minimized else [])
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        async def on_response(resp):
            if not any(m in resp.url for m in API_MARKERS):
                return
            try:
                data = await resp.json()
            except Exception:
                return
            ret = " ".join(str(x) for x in data.get("ret", [])) if isinstance(data, dict) else ""
            if any(sig in ret for sig in THROTTLE_SIGNALS):
                state["stop"], state["site_block"] = ret[:200], True
                return
            state["calls"] += 1
            if state["calls"] > a.max_api_calls:
                state["stop"] = f"достигнут лимит запросов за запуск (--max-api-calls {a.max_api_calls})"
                return
            if a.debug and state["dbg"] < 5:
                state["dbg"] += 1
                (outdir / f"debug_{state['dbg']}.json").write_text(
                    json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            for entry in dig(data, "data", "resultList", default=[]):
                row = parse_item(entry)
                if not row:
                    continue
                state["hits"] += 1
                if row["id"] in items:
                    if state["q"] not in items[row["id"]]["queries"]:
                        items[row["id"]]["queries"] += "; " + state["q"]
                else:
                    row["queries"] = state["q"]
                    items[row["id"]] = row

        page.on("response", on_response)

        async def open_query(q: str):
            """Открывает поиск и терпеливо ждёт результатов, ничего не закрывая."""
            try:
                await page.goto(f"https://www.goofish.com/search?q={quote(q)}",
                                wait_until="domcontentloaded", timeout=a.load_timeout * 1000)
            except Exception as e:
                say("  страница грузится медленно или с ошибкой:", repr(e)[:120], "- жду дальше")
            if any(h in page.url.lower() for h in LOGIN_URL_HINTS):
                say("  сайт показал вход/проверку: пройдите её вручную в окне браузера")
            waited = 0
            while state["hits"] == 0 and waited < a.login_wait and not state["stop"]:
                if waited == 20:
                    say("  результатов пока нет: войдите в аккаунт / пройдите проверку в окне браузера...")
                await page.wait_for_timeout(1000)
                waited += 1

        try:
            for qi, q in enumerate(queries, 1):
                if state["stop"]:
                    break
                state["q"], state["hits"] = q, 0
                say(f"[{qi}/{len(queries)}] {q}")
                try:
                    await open_query(q)

                    # сайт сообщил об ограничении: даём человеку пройти проверку руками
                    tries = 0
                    while state["site_block"] and interactive and tries < a.manual_retries:
                        tries += 1
                        say(f"\n  Сайт показал ограничение: {state['stop']}")
                        say("  Если в окне браузера есть проверка (ползунок, код), пройдите её сами.")
                        ans = await ask("  Enter = повторить этот запрос, q = остановиться: ")
                        if ans == "q":
                            break
                        state["stop"], state["site_block"], state["hits"] = "", False, 0
                        await open_query(q)
                    if state["stop"]:
                        break

                    if state["hits"] == 0:
                        say("  пропускаю запрос (нет ответа API)")
                        continue

                    stale = 0
                    for _ in range(a.pages - 1):
                        before = state["hits"]
                        nxt = page.get_by_text("下一页")
                        try:
                            if await nxt.count() > 0:
                                await nxt.first.click(timeout=3000)
                            else:
                                raise RuntimeError("no next button")
                        except Exception:
                            await page.mouse.wheel(0, 7000)
                            await page.keyboard.press("End")
                        await page.wait_for_timeout(random.randint(int(a.delay * 700), int(a.delay * 1400)))
                        stale = stale + 1 if state["hits"] == before else 0
                        if stale >= 2 or state["stop"]:
                            break
                    say(f"  ответов: {state['hits']}, всего уникальных: {len(items)}")
                    write_csv(outdir / "all_items.csv", list(items.values()),
                              ["status", "prev_price", "id", "title", "price_cny", "city", "seller",
                               "want", "free_ship", "tags", "posted", "drop", "url", "queries"])
                    await page.wait_for_timeout(random.randint(int(a.delay * 1000), int(a.delay * 2500)))
                except Exception as e:
                    say("  ошибка на этом запросе, иду дальше:", repr(e)[:150])
                    continue
        finally:
            if state["stop"]:
                say(f"\n!!! ОСТАНОВКА: {state['stop']}")
                if state["site_block"]:
                    set_cooldown(outdir / "cooldown.json", a.cooldown_hours, state["stop"])
                    say(f"Сайт сообщил об ограничении. Парсер не запустится {a.cooldown_hours:g} ч "
                          "(пауза для безопасности аккаунта).")
                say("Всё собранное до остановки сохранено.")
            if interactive and not a.auto_close:
                try:
                    await ask("\nГотово. Окно браузера оставлено открытым. Нажмите Enter, чтобы закрыть его...")
                except Exception:
                    pass
            await ctx.close()
    return items


async def fetch_details(a, rows: list[dict], outdir: Path):
    """Открывает карточки и достаёт описание (best effort)."""
    from playwright.async_api import async_playwright

    got = {"desc": ""}
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            a.profile, headless=a.headless, locale="zh-CN",
            channel="chrome" if a.chrome else None,
            chromium_sandbox=not a.disable_sandbox,
            viewport={"width": 1366, "height": 900},
            args=["--start-minimized"] if a.minimized else [])
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        async def on_response(resp):
            if any(m in resp.url for m in DETAIL_MARKERS):
                try:
                    data = await resp.json()
                except Exception:
                    return
                d = find_key(data, ["desc", "description", "itemDesc"])
                if d:
                    got["desc"] = str(d)

        page.on("response", on_response)
        for i, row in enumerate(rows, 1):
            got["desc"] = ""
            say(f"  карточка {i}/{len(rows)}: {row['title'][:40]}")
            try:
                await page.goto(row["url"], wait_until="domcontentloaded", timeout=60000)
                for _ in range(10):
                    if got["desc"]:
                        break
                    await page.wait_for_timeout(1000)
            except Exception as e:
                say("   ошибка:", e)
            row["desc"] = got["desc"].replace("\n", " ")[:600]
            await page.wait_for_timeout(random.randint(int(a.delay * 1000), int(a.delay * 2500)))
        await ctx.close()


# ---------------------------------------------------------------- расширение Chrome
# ---------------------------------------------------------------- рынок РФ: цены из публичных TG-каналов и площадок
# Собирает app.pyw через свой браузер: t.me/s/<канал> (публичная веб-версия, без входа) и страницы Oskelly /
# Second Friend, которые вы сами открыли. Это цены, которые ПРОСЯТ, поэтому в предложение идёт медиана × factor.
MARKET_FILE = Path(__file__).with_name("market.json")
MARKET_DEFAULTS = {"channels": ["graills", "amanexoexo", "underunique", "gurbichstore", "anteroomstore", "asexchina", "amahasla",
                                "risskiss_resale", "TCMmarcet", "swoosh_sell"], "factor": 0.85, "every_days": 7, "pages": 5, "last_run": ""}
MARKET_BUY = ["куплю", "скупка", "скупаю", "выкуп", "выкупаю", "в поиске", "ищу ", "wtb", "обмен"]
MARKET_SOLD = ["продано", "sold", "ушло", "ушла", "ушёл", "ушел"]
RU_BRANDS = {"prada": ["прада"], "margiela": ["маржела", "маржиела", "марджела"], "golden_goose": ["голден гус", "гуси"],
             "bottega": ["боттега", "ботега"], "rick_owens": ["рик оуэнс", "рик овенс", "рик оуенс"],
             "balenciaga": ["баленсиага"], "stone_island": ["стон айленд", "стоун айленд", "стонник", "стоник"],
             "moncler": ["монклер"], "chrome_hearts": ["хром хартс", "хромхартс"], "lv": ["луи виттон", "луи вуиттон"]}
RU_CATS = {"outerwear": ["куртк", "пуховик", "бомбер", "парка", "пальто", "ветровк", "жилет", "анорак", "дублёнк", "дубленк", "косух"],
           "clothing": ["свитер", "худи", "свитшот", "футболк", "лонгслив", "рубашк", "брюк", "штаны", "джинс", "шорты", "кофт",
                        "джемпер", "кардиган", "поло", "овершот", "зипк", "толстовк"],
           "sneakers": ["кроссовк", "кеды", "сникер"], "shoes": ["ботинк", "сапог", "туфл", "лофер", "дерби", "челси", "сандал", "мюли"],
           "bag": ["сумк", "рюкзак", "мессенджер", "клатч", "кошел", "бананк", "портфел", "шоппер", "кросс-боди"],
           "accessories": ["ремень", "кепк", "шапк", "шарф", "перчат", "бейсболк"], "eyewear": ["очки"],
           "jewelry": ["кольц", "цепь", "цепоч", "браслет", "подвеск", "серьг", "кулон"]}
# русские типы одежды -> слова строгих моделей каталога (свитер Stone Island = «毛衣»)
RU_TYPES = [(("свитер", "джемпер", "кардиган", "трикотаж"), "毛衣"), (("худи",), "卫衣 连帽"), (("свитшот", "толстовк"), "卫衣"),
            (("рубашк", "овершот"), "衬衫"), (("шорты",), "短裤"), (("брюк", "штаны", "карго", "джоггер"), "裤"),
            (("футболк", "поло", "лонгслив"), "t恤")]
# новая вещь из магазина, а не перепродажа б/у: скидка от розницы, размерный ряд, «под заказ» — в цену продажи не идёт
_NEW_STOCK = re.compile(r"-\s?\d{1,2}\s?%|(?<![a-z])(?:xxs|xs|s|m|l|xl|2xl|xxl|3xl)(?:\s?/\s?(?:xxs|xs|s|m|l|xl|2xl|xxl|3xl)){2,}(?![a-z])"
                        r"|под\s?заказ|предзаказ", re.I)
MARKET_RETAIL = ["цум", "ретейл", "ритейл", "retail", "в магазине", "в бутике", "было ", "оригинальная цена", "новая стоила"]
_RUB = r"(?:₽|руб(?:лей|ля|\.)?|р\.|р(?![a-zа-я])|rub)"
_NUM = r"(\d{1,3}(?:[   .,]\d{3})+|\d{3,7})"
_PRICE_RX = [re.compile(r"(?<![\d.,])" + _NUM + r"\s*" + _RUB, re.I),
             re.compile(r"(?<![\d.,])(\d{1,3}(?:[.,]\d)?)\s*[кk](?:\s*" + _RUB + r")?(?![a-zа-я0-9])", re.I),
             re.compile(r"цен[аы][^\d\n]{0,12}" + _NUM, re.I)]


def load_market_cfg() -> dict:
    try:
        c = json.loads(MARKET_FILE.read_text(encoding="utf-8"))
    except Exception:
        c = {}
    return clean_market_cfg({**MARKET_DEFAULTS, **c})


def clean_market_cfg(c: dict) -> dict:
    chans = []
    raw = c.get("channels") or []
    for x in (re.split(r"[\s,;]+", raw) if isinstance(raw, str) else raw):
        m = re.search(r"(?:t\.me/(?:s/)?|@)?([A-Za-z][A-Za-z0-9_]{3,40})/?$", str(x).strip())
        if m and m.group(1).lower() not in (ch.lower() for ch in chans):
            chans.append(m.group(1))
    num_ = lambda k, lo, hi: min(max(to_float(c.get(k, MARKET_DEFAULTS[k])), lo), hi)
    imp = c.get("imported") if isinstance(c.get("imported"), dict) else {}
    return {"channels": chans[:30], "factor": round(num_("factor", 0.5, 1.0), 2), "every_days": int(num_("every_days", 1, 60)),
            "pages": int(num_("pages", 1, 20)), "last_run": str(c.get("last_run") or ""),
            "imported": dict(list(imp.items())[-300:]), "import_last": str(c.get("import_last") or ""),
            "import_note": str(c.get("import_note") or "")[:300]}


def save_market_cfg(c: dict) -> dict:
    c = clean_market_cfg(c)
    MARKET_FILE.write_text(json.dumps(c, ensure_ascii=False, indent=1), encoding="utf-8")
    return c


def post_prices(text: str) -> list[int]:
    """Все цены в рублях из текста поста: «15 000 ₽», «15.000 руб», «15к», «цена: 15000»."""
    out = []
    for i, rx in enumerate(_PRICE_RX):
        for m in rx.finditer(text):
            s = re.sub(r"[   ]", "", m.group(1))
            v = float(s.replace(",", ".")) * 1000 if i == 1 else float(re.sub(r"[.,]", "", s))
            if 1500 <= v <= 1_500_000 and int(v) not in out:
                out.append(int(v))
    return out


def parse_market_post(text: str, cat: dict, shop: bool = False, outside: bool = False) -> dict | None:
    """Пост канала / карточка площадки -> бренд, модель, тип, цена. Сборные посты и «куплю» не берём.
    outside=True — ещё и бренды из списка «интересных вне каталога»: у них brand пустой, имя — в bname."""
    t = low(text)
    if not shop and has_any(t, MARKET_BUY):
        return None
    for bk, ws in RU_BRANDS.items():
        if bk in cat["brands"]:
            for w in ws:
                t = t.replace(w, " " + cat["brands"][bk]["aliases"][0] + " ")
    prices = post_prices(text)
    if not shop:
        m = _PRICE_RX[2].search(text)
        if len(prices) > 1 and m:      # «цена 15 000, в ЦУМе 60 000» -> берём ту, что после «цена»
            prices = [int(float(re.sub(r"[   .,]", "", m.group(1))))]
        elif len(prices) == 2 and has_any(t, MARKET_RETAIL):    # вторая — цена в магазине, она выше
            prices = [min(prices)]
        if len(prices) != 1:
            return None
    elif not prices:
        return None
    title = t.split("\n", 1)[0][:140]
    for stems, cn in RU_TYPES:
        if any(s in title for s in stems):
            title += " " + cn
        if any(s in t for s in stems):
            t += " " + cn
    bk, _ = detect_brand(title, t, cat)
    ck = detect_category(title, t, cat) or next((k for k, ws in RU_CATS.items() if any(w in t for w in ws)), "")
    if not bk:
        hit = outside_brand(title + " " + t, cat) if outside else None
        if not hit or hit[1] != 1:
            return None
        return {"brand": "", "bname": cat["outside"]["interesting"][hit[2]]["name"], "model": "", "ckey": ck,
                "price_rub": min(prices), "sold": bool(has_any(t, MARKET_SOLD)), "stock": bool(_NEW_STOCK.search(text))}
    b = cat["brands"][bk]
    model = detect_model(t, b, title)
    if model and model.get("strict") and ck not in ("", model.get("category")):
        model = None
    return {"brand": bk, "bname": b["name"], "model": model["name"] if model else "", "ckey": (model or {}).get("category") or ck,
            "price_rub": min(prices), "sold": bool(has_any(t, MARKET_SOLD)), "stock": bool(_NEW_STOCK.search(text))}


def market_ingest(db, source: str, items: list, limit: int = 200) -> int:
    now, n = datetime.now().isoformat(timespec="seconds"), 0
    for it in items[:limit]:
        key, text = f'{source}:{it.get("id", "")}', str(it.get("text", ""))[:3000]
        if not it.get("id") or not text.strip():
            continue
        cur = db.execute("SELECT 1 FROM market WHERE key=?", (key,)).fetchone()
        db.execute("INSERT OR REPLACE INTO market(key, source, url, ts, seen, text) VALUES(?,?,?,?,?,?)",
                   (key, source, str(it.get("url", ""))[:300], str(it.get("date") or now)[:19], now, text))
        n += cur is None
    db.commit()
    return n


def market_rows(db, cat: dict, days: int = 120, outside: bool = False) -> list[dict]:
    """Разобранные объявления рынка за последние days дней; повторы одного текста — один раз."""
    since = (datetime.now() - timedelta(days=days)).isoformat()
    out, seen = [], set()
    for source, url, ts, text in db.execute("SELECT source, url, ts, text FROM market WHERE ts >= ? ORDER BY ts DESC", (since,)):
        h = re.sub(r"\W+", "", low(text))[:200]
        if h in seen:
            continue
        seen.add(h)
        p = parse_market_post(text, cat, shop=source.startswith("shop:"), outside=outside)
        if p:
            out.append(dict(p, source=source, url=url, ts=ts))
    return out


_MSELL = {"key": None, "have": {}}
MKT_MIN_TYPE = 8            # бренд и тип вещи: меньше 8 объявлений — пара дорогих архивных вещей решает всё


def market_sell_table(db, cat: dict) -> dict:
    """Цены продажи от рынка России -> то, что кладётся в MARKET_SELL. Модель: медиана от 3 объявлений. Бренд и тип
    вещи: от 8 объявлений и ниже середины (между нижней четвертью и медианой) — в такой куче новые дорогие вещи
    тянут медиану вверх, а покупаем мы б/у. Всё умножается на factor («просят → продают»)."""
    f = load_market_cfg()["factor"]
    groups: dict = {}
    have = _MSELL["have"] = {}      # (бренд, тип) -> сколько объявлений б/у уже есть (для списка «не хватает цен России»)
    for r in market_rows(db, cat, outside=True):
        if r["stock"]:
            continue
        have[(r["bname"], r["ckey"])] = have.get((r["bname"], r["ckey"]), 0) + 1
        if r["model"]:
            groups.setdefault(("m", r["bname"], r["model"]), []).append(r["price_rub"])
        if r["ckey"]:
            groups.setdefault(("c", r["bname"], r["ckey"]), []).append(r["price_rub"])
    out = {}
    for k, ps in groups.items():
        if k[0] == "m" and len(ps) >= 3:
            v = statistics.median(ps)
        elif k[0] == "c" and len(ps) >= MKT_MIN_TYPE:
            q = statistics.quantiles(ps, n=4)
            v = (q[0] + q[1]) / 2
        else:
            continue
        out[k] = (round(v * f / 500) * 500, len(ps))
    return out


AVITO_TYPE = {"outerwear": "куртка", "bag": "сумка", "sneakers": "кроссовки", "shoes": "ботинки", "eyewear": "очки"}


def market_gaps(good: list[dict], cat: dict, limit: int = 12) -> list[dict]:
    """«Не хватает цен России»: бренд и тип вещи, которых в ленте много, а цена продажи — заглушка. Сколько объявлений б/у
    уже собрано, сколько нужно и готовый поиск Avito (откроется во вкладке Avito по вашему нажатию)."""
    import unicodedata
    groups: dict = {}
    for r in good:
        if r.get("sell_src") == "stub" and r.get("ckey") not in ("", "?", None):
            g = groups.setdefault((r["brand"], r["ckey"]), {"brand": r["brand"], "ckey": r["ckey"], "type": r.get("category", ""), "n": 0})
            g["n"] += 1
    out = []
    for (b, ck), g in sorted(groups.items(), key=lambda x: -x[1]["n"])[:limit]:
        name = re.sub(r"\s*\(.*?\)", "", "".join(c for c in unicodedata.normalize("NFKD", b) if not unicodedata.combining(c)))
        q = (name + " " + AVITO_TYPE.get(ck, "")).strip()
        out.append(dict(g, have=_MSELL["have"].get((b, ck), 0), need=MKT_MIN_TYPE, q=q))
    return out


def refresh_market_sell(db, cat: dict) -> bool:
    """Пересчитывает MARKET_SELL, когда в таблице market появились новые объявления; True — цены изменились
    (тогда ленту надо переоценить)."""
    key = (db.execute("SELECT COUNT(*), MAX(seen) FROM market").fetchone(), load_market_cfg()["factor"],
           hashlib.md5(json.dumps(cat, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest())
    if key == _MSELL["key"]:
        return False
    first = _MSELL["key"] is None       # первый расчёт после запуска: таблица в памяти ещё пустая
    _MSELL["key"] = key
    new = market_sell_table(db, cat)
    same = new == MARKET_SELL
    if not same:
        MARKET_SELL.clear()
        MARKET_SELL.update(new)
    if not first and same:
        return False
    # При запуске сравниваем не с пустой таблицей, а с тем, что было в прошлый сеанс: иначе каждый старт
    # возвращал из архива все отсеянные за 3 дня вещи (десятки тысяч) и пересчитывал их зря.
    sig = _msell_sig(new, key[2])
    sig_file = _msell_sig_file(db)
    if first and sig_file is not None:
        try:
            same = sig_file.read_text(encoding="utf-8").strip() == sig
        except OSError:
            same = False
    if sig_file is not None and not same:
        try:
            sig_file.write_text(sig, encoding="utf-8")
        except OSError:
            pass
    return not same


def _msell_sig(table: dict, cat_hash: str) -> str:
    """Отпечаток всего, от чего зависит вердикт уже отсеянной вещи между запусками: цены рынка, каталог, код правил."""
    code = []
    for name in ("gp_parse.py", "gp_score.py", "gp_market.py", "gp_process.py"):
        try:
            st = Path(__file__).with_name(name).stat()
            code.append((name, st.st_size, st.st_mtime_ns))
        except OSError:
            code.append((name, 0, 0))
    body = json.dumps([sorted((list(k), list(v) if isinstance(v, (tuple, list)) else v) for k, v in table.items()),
                       cat_hash, code], ensure_ascii=False, default=str)
    return hashlib.md5(body.encode()).hexdigest()


def _msell_sig_file(db):
    """Файл отпечатка лежит рядом с базой (out/msell.sig); у базы в памяти его нет."""
    try:
        path = db.execute("PRAGMA database_list").fetchone()[2]
    except Exception:
        return None
    return Path(path).with_name("msell.sig") if path else None


def market_suggestions(db, cat: dict) -> list[dict]:
    """Предложения для вкладки «Цены» в том же виде, что price_suggestions.json."""
    fmt = lambda v: f"{v:,.0f}".replace(",", " ")
    f = load_market_cfg()["factor"]
    groups: dict = {}
    for r in market_rows(db, cat):
        key = (r["brand"], "m", r["model"]) if r["model"] else (r["brand"], "c", r["ckey"])
        if key[2]:
            groups.setdefault(key, []).append(r)
    out = []
    for (bk, kind, name), rs in groups.items():
        if len(rs) < 3 or (kind == "c" and not cat["categories"].get(name, {}).get("enabled", True)):
            continue
        ps = sorted(r["price_rub"] for r in rs)
        med = statistics.median(ps)
        srcs = sorted({r["source"].split(":", 1)[1] for r in rs})
        s = {"brand": bk, "value": int(round(med * f / 500) * 500), "kind": "market",
             "why": f"Рынок: {len(rs)} объявл. за 4 мес. ({', '.join(srcs[:4])}), просят {fmt(ps[0])}–{fmt(ps[-1])} ₽, "
                    f"медиана {fmt(med)} ₽ × {f} («просят → продают»)."
                    + (f" Из них с пометкой «продано»: {sum(r['sold'] for r in rs)}." if any(r["sold"] for r in rs) else ""),
             "sources": [r["url"] for r in rs if r["url"].startswith("http")][:3]}
        s["model" if kind == "m" else "category"] = name
        out.append(s)
    return out


# Импорт из Telegram Desktop: канал → ⋮ → «Экспорт истории чата» (HTML или JSON). Нужен, когда t.me с этого
# компьютера не открывается. Читаются только экспорты: tg_export/ рядом с программой и Загрузки\Telegram Desktop\ChatExport_*.
TG_EXPORT_DIRS = [(Path(__file__).with_name("tg_export"), ("", "*/")),
                  (Path.home() / "Downloads" / "Telegram Desktop", ("ChatExport_*/",))]
TG_EXPORT_DAYS = 180


def tg_export_files() -> list[Path]:
    out = set()
    for d, subs in TG_EXPORT_DIRS:
        if d.is_dir():
            for sub in subs:
                for pat in ("result.json", "messages*.html"):
                    out.update(d.glob(sub + pat))
    return sorted(out)


def _tg_text(t) -> str:
    """Текст сообщения из JSON-экспорта: строка или список кусков (ссылки, жирный и т. п.)."""
    if isinstance(t, list):
        return "".join(x if isinstance(x, str) else str(x.get("text", "")) for x in t)
    return str(t or "")


def read_tg_export(f: Path) -> list[tuple[str, list]]:
    """Файл экспорта -> [(источник, посты)]. Берём только каналы и группы, личные переписки — нет."""
    import html as html_mod
    since = (datetime.now() - timedelta(days=TG_EXPORT_DAYS)).isoformat()
    out = []
    if f.suffix == ".json":
        j = json.loads(f.read_text(encoding="utf-8"))
        for ch in ([j] if "messages" in j else (j.get("chats") or {}).get("list") or []):
            if not re.search(r"channel|supergroup", str(ch.get("type", ""))):
                continue
            cid, items = ch.get("id"), []
            for m in ch.get("messages") or []:
                text = _tg_text(m.get("text"))
                if m.get("type") == "message" and text.strip() and str(m.get("date", "")) >= since:
                    items.append({"id": str(m["id"]), "date": m["date"], "text": text,
                                  "url": f"https://t.me/c/{cid}/{m['id']}" if cid else ""})
            out.append(("tg:" + str(ch.get("name") or cid)[:60], items))
        return out
    raw = f.read_text(encoding="utf-8", errors="replace")
    strip = lambda s: html_mod.unescape(re.sub(r"<[^>]+>", "", re.sub(r"<br\s*/?>", "\n", s))).strip()
    m = re.search(r'<div class="page_header">.*?<div class="text bold">(.*?)</div>', raw, re.S)
    name, items, authors = strip(m.group(1)) if m else f.parent.name, [], []
    for chunk in re.split(r'<div class="message default clearfix[^"]*" id="message', raw)[1:]:
        mid = re.match(r"-?\d+", chunk)
        d = re.search(r'class="pull_right date details" title="(\d\d)\.(\d\d)\.(\d{4}) (\d\d:\d\d:\d\d)', chunk)
        tx = re.search(r'<div class="text">(.*?)</div>', chunk, re.S)
        fr = re.search(r'<div class="from_name">(.*?)</div>', chunk, re.S)
        if fr:
            authors.append(strip(fr.group(1)))
        if mid and d and tx:
            date_ = f"{d.group(3)}-{d.group(2)}-{d.group(1)}T{d.group(4)}"
            if date_ >= since and strip(tx.group(1)):
                items.append({"id": mid.group(0), "date": date_, "text": strip(tx.group(1)), "url": ""})
    # у канала автор почти каждого поста — сам канал; у личной переписки авторов двое
    if authors and sum(x == name for x in authors) < 0.8 * len(authors):
        return []
    return [("tg:" + name[:60], items)]


def import_tg_exports(db) -> str:
    """Новые и изменённые файлы экспорта -> таблица market. Вызывается под lock. Возвращает итог для лога/экрана."""
    c = load_market_cfg()
    done, files, posts, new, chans = c["imported"], 0, 0, 0, []
    old = time.time() - TG_EXPORT_DAYS * 86400
    for f in tg_export_files():
        st = f.stat()
        sig = f"{int(st.st_mtime)}:{st.st_size}"
        if done.get(str(f)) == sig or st.st_mtime < old:
            continue
        done.pop(str(f), None)
        done[str(f)] = sig
        files += 1
        try:
            chats = read_tg_export(f)
        except Exception as e:
            say(f"Рынок: экспорт {f.parent.name}/{f.name} не прочитан: {repr(e)[:120]}")
            continue
        if not chats:
            say(f"Рынок: {f.parent.name}/{f.name} — личная переписка или пусто, пропускаю")
        for src, items in chats:
            posts += len(items)
            new += market_ingest(db, src, items, limit=50000)
            if src[3:] not in chans:
                chans.append(src[3:])
    if not files:
        return ""
    note = f"файлов {files}, постов за {TG_EXPORT_DAYS // 30} мес. {posts}, новых {new}" + (f" ({', '.join(chans[:6])})" if chans else "")
    c.update(imported=done, import_last=datetime.now().isoformat(timespec="seconds"), import_note=note)
    save_market_cfg(c)
    say("Рынок: импорт из Telegram Desktop —", note)
    return note


def market_cfg_public(c: dict) -> dict:
    """Настройки рынка для страницы: без списка прочитанных файлов, зато с папками импорта."""
    return {**{k: v for k, v in c.items() if k != "imported"},
            "import_dirs": [str(d) + ("\\ChatExport_…" if "*" in subs[0] else "") for d, subs in TG_EXPORT_DIRS]}


def market_stats(db, cat: dict) -> dict:
    total = db.execute("SELECT COUNT(*) FROM market").fetchone()[0]
    by = dict(db.execute("SELECT source, COUNT(*) FROM market GROUP BY source").fetchall())
    return {"posts": total, "parsed": len(market_rows(db, cat, days=3650)), "by_source": by}


# ---------------------------------------------------------------- сохранённые поиски и автопоиск
SEARCHES_FILE = Path(__file__).with_name("searches.json")
# порядок площадок в круге автопоиска; пусто — вперемешку: поиски Mercari равномерно между поисками Goofish
WATCH_ORDERS = ("goofish_first", "mercari_first", "goofish_only", "mercari_only")
SEARCH_MAX = 80             # «Мои поиски»: больше — круг автопоиска растягивается
MERCARI_MIN = 8000          # поиск Mercari по умолчанию «от ¥8 000»: дешевле с посредником почти никогда не окупается
WATCH_MIN_INTERVAL = 3      # автопоиск: полный круг не чаще, мин


def load_searches() -> dict:
    """{"interval_min": 15, "items": [{"q": "stone island 毛衣", "max": 1500}]} — «max» только для уведомлений."""
    try:
        s = json.loads(SEARCHES_FILE.read_text(encoding="utf-8"))
    except Exception:
        s = {}
    if any(not x.get("since") for x in s.get("items") or []):     # дату добавления старым поискам ставим один раз
        try:
            return save_searches(s)
        except Exception:
            pass
    return clean_searches(s)


def clean_searches(s: dict) -> dict:
    items, seen = [], set()
    src = s.get("items") or []
    # поисков до SEARCH_MAX и отдельно продавцов до 30: подписки не вытесняют поиски
    src = [x for x in src if not str(x.get("seller") or "").isdigit()][:SEARCH_MAX] + \
          [x for x in src if str(x.get("seller") or "").isdigit()][:30]
    for it in src:
        q = re.sub(r"\s+", " ", str(it.get("q", ""))).strip()[:60]
        site = "mercari" if it.get("site") == "mercari" else ""     # площадка: Goofish (по умолчанию) или Mercari
        if not q or (site, q.lower()) in seen:
            continue
        seen.add((site, q.lower()))
        try:
            mx = float(it.get("max") or 0)
        except (TypeError, ValueError):
            mx = 0
        try:
            dp = min(max(int(it.get("depth") or 1), 1), 3)     # сколько страниц выдачи листает автопоиск
        except (TypeError, ValueError):
            dp = 1
        sid = str(it.get("seller") or "")     # «Мои продавцы»: автопоиск открывает профиль по числовому id
        alt = re.sub(r"\s+", " ", str(it.get("alt") or "")).strip()[:60]
        items.append({"q": q, "max": round(mx) if mx > 0 else None, "depth": 1 if sid.isdigit() else dp}
                     | ({"rare": True} if it.get("rare") else {})       # автопоиск заходит раз в 3 круга
                     | ({"pin": True} if it.get("pin") and not it.get("rare") else {})     # всегда каждый круг (сильнее автоматики)
                     | {"since": str(it.get("since") or date.today().isoformat())[:10]}   # когда добавлен: новому — 3 дня форы
                     | ({"seller": sid} if sid.isdigit() else {})
                     | ({"site": site} if site else {})
                     # Mercari: нижняя цена прямо в адресе поиска — дешёвый мусор не занимает страницу выдачи
                     | ({"min": _int0(it.get("min"), MERCARI_MIN)} if site == "mercari" and not sid.isdigit() else {})
                     # второй вариант запроса (иероглифы / латиница): пока не выбран лучший, автопоиск чередует их
                     | ({"alt": alt} if alt and low(alt) != low(q) else {})
                     | ({"picked": True, "why": str(it.get("why") or "")[:160]} if alt and it.get("picked") else {})
                     | ({"src": "каналы", "hot_day": str(it.get("hot_day") or "")[:10]} if it.get("src") == "каналы" else {})
                     # «из избранного»: запрос Gemini по фото ★ (fav — номера этих вещей, day — когда добавлен)
                     | ({"src": "избранное", "fav": [str(i) for i in it.get("fav") or []][:30],
                         "day": str(it.get("day") or "")[:10]} if it.get("src") == "избранное" else {}))
    try:
        iv = int(float(s.get("interval_min") or 15))
    except (TypeError, ValueError):
        iv = 15
    order = s.get("order") if s.get("order") in WATCH_ORDERS else ""     # порядок площадок в круге автопоиска
    return {"interval_min": min(max(iv, WATCH_MIN_INTERVAL), 240), "items": items} | ({"order": order} if order else {})


def _int0(v, default: int) -> int:
    """«от ¥»: число ≥ 0; не задано — default (0 — без нижней цены)."""
    if v is None or v == "":
        return default
    try:
        return max(int(float(v)), 0)
    except (TypeError, ValueError):
        return default


def save_searches(s: dict) -> dict:
    s = clean_searches(s)
    SEARCHES_FILE.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
    return s


# ---------------------------------------------------------------- заказы клиентов
# «Runner 43, клиент платит 35 000 ₽» -> подходящие вещи ленты и уведомление о новых. Прибыль считается так же,
# как в ленте, только вместо цены продажи из каталога — бюджет клиента.
ORDERS_FILE = Path(__file__).with_name("orders.json")


def load_orders() -> dict:
    try:
        o = json.loads(ORDERS_FILE.read_text(encoding="utf-8"))
    except Exception:
        o = {}
    return clean_orders(o)


def clean_orders(o: dict) -> dict:
    items = []
    for x in (o.get("items") or [])[:100]:
        if not isinstance(x, dict):
            continue
        it = {k: str(x.get(k) or "").strip()[:80] for k in ("id", "client", "model", "words", "size", "note")}
        # несколько брендов — через « | »: Stone Island | Moncler; модель имеет смысл только при одном бренде
        bs = [b.strip() for b in str(x.get("brand") or "").split("|") if b.strip()]
        it["brand"] = " | ".join(dict.fromkeys(bs))[:400]
        if len(bs) > 1:
            it["model"] = ""
        it["gender"] = x.get("gender") if x.get("gender") in ("м", "ж") else ""      # только мужское / только женское
        due = str(x.get("due") or "")[:10]
        it["due"] = due if re.fullmatch(r"\d{4}-\d\d-\d\d", due) else ""               # нужно к дате
        it["budget"] = int(max(to_float(x.get("budget")), 0))
        it["budget_min"] = int(min(max(to_float(x.get("budget_min")), 0), it["budget"]))    # вещи с затратами ниже — не показывать
        it["active"] = bool(x.get("active", True))
        it["created"] = str(x.get("created") or datetime.now().isoformat(timespec="seconds"))[:19]
        if not it["id"]:
            it["id"] = hashlib.md5(f'{it["client"]}{it["created"]}{len(items)}'.encode()).hexdigest()[:8]
        if it["budget"] and (it["brand"] or it["words"]):
            items.append(it)
    return {"items": items}


def save_orders(o: dict) -> dict:
    o = clean_orders(o)
    ORDERS_FILE.write_text(json.dumps(o, ensure_ascii=False, indent=1), encoding="utf-8")
    return o


def _size_key(s: str) -> str:
    s = low(str(s)).replace("码", "").replace("eu", "").replace("(it)", "").replace(" ", "")
    return {"xs": "xs", "s": "s", "m": "m", "l": "l", "xl": "xl", "xxl": "xxl", "2xl": "xxl", "3xl": "xxxl", "xxxl": "xxxl"}.get(s, s)


def order_fit(o: dict, r: dict) -> str:
    """"" — не подходит; "да" — подходит; "размер?" — всё совпало, но размер в объявлении не указан."""
    if o["brand"] and not {r.get("brand"), r.get("outside_brand")} & {b.strip() for b in o["brand"].split("|")}:
        return ""
    if o.get("budget_min") and (r.get("cost_rub") or 0) < o["budget_min"]:      # слишком дешёвое для этого клиента
        return ""
    if o.get("gender") and r.get("gender") and r["gender"] != o["gender"]:      # пол не ясен — вещь остаётся
        return ""
    if o["model"] and r.get("model") != o["model"]:
        return ""
    if o["words"]:
        t = low(f'{r.get("title", "")} {r.get("desc", "")}')
        # слова через пробел — нужны все; варианты одного слова через «|» — любой (羽绒|ダウン: Китай и Япония пишут по-разному)
        if not all(any(find_pos(t, v) >= 0 for v in w.split("|") if v) for w in low(o["words"]).split()):
            return ""
    if r.get("mark") == "hide" or r.get("fake_risk") == "высокий":
        return ""
    if o["size"]:
        if not r.get("size"):
            return "размер?"
        if _size_key(r["size"]) not in {_size_key(x) for x in re.split(r"[/,|]", o["size"]) if x.strip()}:     # «M/L» — любой из размеров
            return ""
    return "да"


# Мужское / женское: только пометка и фильтр, на оценку не влияет. Сначала «унисекс», потом женские слова
# (в «women» есть «men»), потом мужские; у Moncler — ещё и названия моделей.
G_UNI = ["男女", "中性", "unisex", "情侣", "ユニセックス", "男女兼用"]
G_WOMEN = ["女士", "女款", "女包", "女生", "女式", "少女", "女装", "女鞋", "レディース", "ladies", "women", "womens", "woman", "女性"]
G_MEN = ["男士", "男款", "男包", "男式", "男装", "男鞋", "男生", "メンズ", "mens", "men", "男性"]
G_MODELS = {"Moncler": {"ж": ["bady", "lans", "suyen", "hermine", "khloe", "aphroti", "armoise", "maire", "クレア", "セザンヌ"],
                        "м": ["maya", "montcla", "montgenevre", "ecrins", "cuvellier", "veillat", "マヤ"]}}


def gender_of(r: dict) -> str:
    """"ж" — женская вещь, "м" — мужская, "" — не ясно или унисекс."""
    t = low(f'{r.get("title", "")} {r.get("desc", "")}')
    if any(w in t for w in G_UNI):
        return ""
    if any(w in t for w in G_WOMEN[:9]) or hits(t, G_WOMEN[9:]):
        return "ж"
    if any(w in t for w in G_MEN[:8]) or hits(t, G_MEN[8:]):
        return "м"
    for g, ws in G_MODELS.get(r.get("brand", ""), {}).items():
        if hits(t, ws):
            return g
    return ""


def order_late(o: dict, r: dict) -> bool:
    """Не успеет к сроку заказа: сегодня + срок доставки с площадки позже «нужно к дате». Срок не задан — не судим."""
    days = COSTS.get("jp_days" if r.get("site") == "mercari" else "cn_days") or 0
    if not o.get("due") or not days:
        return False
    try:
        return date.today() + timedelta(days=int(days)) > date.fromisoformat(o["due"])
    except ValueError:
        return False


# Разбор объявления «ищу…» в заготовку заказа: бренды, размеры, бюджет, тип вещи, контакт
LEAD_WORDS = ["ищу ", "ищем ", "куплю", "в поиске", "wtb", "приветствую! ищу"]
LEAD_ALIASES = {"C.P. Company": ["cp", "c.p", "си пи", "сипи"], "Stone Island": ["stone", "стон", "стоун", "стоник"], "Moncler": ["монклер", "монклеры"],
                "Prada": ["прада"], "Rick Owens": ["рик", "rick"], "Maison Margiela": ["маржела", "margiela", "mm6"],
                "Balenciaga": ["баленсиага", "баленса"], "Chrome Hearts": ["хром хартс", "хромы"], "Arc'teryx": ["арктерикс", "arcteryx"]}
LEAD_TYPES = [("пухов", "羽绒|ダウン|puffer|down"), ("куртк", "夹克|外套|ジャケット|jacket"), ("бомбер", "夹克|ボンバー|bomber"),
              ("худи", "卫衣|パーカー|hoodie"), ("свитшот", "卫衣|スウェット|sweatshirt"), ("свитер", "毛衣|针织|ニット|sweater|knit"),
              ("джинс", "牛仔|デニム|jeans|denim"), ("футболк", "t恤|短袖|tシャツ|tee"), ("кроссовк", "鞋|スニーカー|sneakers"),
              ("рюкзак", "双肩|リュック|backpack"), ("сумк", "包|バッグ|bag")]
_LEAD_K = re.compile(r"(?<![\d.,])(\d{1,3}(?:[.,]\d)?)\s*(?:тыс(?:яч)?\.?|т\.?\s?р\.?|к|k)(?![a-zа-я0-9])", re.I)
_LEAD_SIZE = re.compile(r"(?<![a-zа-я])(xxs|xs|xxl|xl|s|m|l)(?![a-zа-я])", re.I)


def parse_lead(text: str, cat: dict) -> dict:
    """Текст «ищу пуховик, размер m-l, stone island, moncler… до 150 тысяч» -> заготовка заказа. Ничего не сохраняет."""
    t = low(text)
    names = [(b["name"], b.get("aliases", [])) for b in cat["brands"].values()] + \
            [(b["name"], b.get("words", [])) for b in (cat.get("outside") or {}).get("interesting", [])]
    ru = {cat["brands"][k]["name"]: ws for k, ws in RU_BRANDS.items() if k in cat["brands"]}
    found = []
    for name, ws in names:
        ps = [p for p in (find_pos(t, low(w)) for w in list(ws) + ru.get(name, []) + LEAD_ALIASES.get(name, [])) if p >= 0]
        if ps:
            found.append((min(ps), name))
    brands = [n for _, n in sorted(found)]
    # после слова «размер» — до четырёх коротких значений (m-l; 48 и 50; 43); без него — только заглавные S/M/L/XL в тексте
    m = re.search(r"размер\w*[\s:–-]*((?:[a-z0-9.]{1,4}(?:\s*[,/–-]\s*|\s+и\s+|\s+)?){1,4})", t)
    sizes = [s.upper() for s in _LEAD_SIZE.findall(m.group(1))] if m else []
    sizes += re.findall(r"(?<![\d.])(3[5-9]|4\d|5[0-6])(?![\d.])", m.group(1)) if m else []
    if not sizes:
        sizes = re.findall(r"(?<![A-Za-zА-Яа-яЁё0-9])(XXS|XS|XXL|XL|S|M|L)(?![A-Za-zА-Яа-яЁё0-9])", text)
    nums = [int(float(x.replace(",", ".")) * 1000) for x in _LEAD_K.findall(text)] or post_prices(text)
    rng = re.search(r"от\s*(\d{1,3})\s*(?:до|–|-)\s*\d{1,3}(?:[.,]\d)?\s*(?:тыс|т\.?\s?р|к|k)", t)     # «от 30 до 45к»: тысячи названы один раз
    if rng:
        nums.append(int(rng.group(1)) * 1000)
    nums = sorted(n for n in nums if n >= 3000)
    h = re.findall(r"@[A-Za-z][A-Za-z0-9_]{3,}", text)
    words = next((w for stem, w in LEAD_TYPES if stem in t), "")
    return {"brand": " | ".join(dict.fromkeys(brands)), "size": ", ".join(dict.fromkeys(sizes)), "words": words,
            "budget": nums[-1] if nums else 0, "budget_min": 0,     # «от 30» у покупателя — его цена, а «затраты от» в заказе — ваши расходы: не подставляем
            "gender": "ж" if "женск" in t else "м" if "мужск" in t else "", "client": h[-1] if h else "",
            "note": re.sub(r"\s+", " ", text).strip()[:80]}


_LEADS = {"key": None, "rows": []}


def market_leads(db, days: int = 45, limit: int = 20, cat: dict | None = None) -> list[dict]:
    """«Ищут»: посты каналов и чатов (в т. ч. из экспорта Telegram) со словами «ищу / куплю / в поиске», в которых
    распознан бренд и есть размер или бюджет. Пересчёт — только при новых постах."""
    key = db.execute("SELECT COUNT(*), MAX(seen) FROM market").fetchone()
    if key == _LEADS["key"]:
        return _LEADS["rows"]
    since = (datetime.now() - timedelta(days=days)).isoformat()
    out = []
    for source, url, ts, text in db.execute("SELECT source, url, ts, text FROM market WHERE ts >= ? AND source LIKE 'tg:%' ORDER BY ts DESC", (since,)):
        if not has_any(low(text), LEAD_WORDS):
            continue
        lead = parse_lead(text, cat)
        if lead["brand"] and (lead["budget"] or lead["size"]):
            out.append({"src": source[3:], "ts": ts[:10], "url": url, "text": re.sub(r"\s+", " ", text).strip()[:220], "lead": lead})
        if len(out) >= limit:
            break
    _LEADS.update(key=key, rows=out)
    return out


# ---------------------------------------------------------------- доступ с телефона (Tailscale)
# Приложение слушает ещё и адрес Tailscale (100.64.0.0/10 — закрытая сеть только ваших устройств) и пускает
# туда только с секретным ключом: ссылка ?t=ключ один раз, дальше ключ в cookie телефона.
def tailscale_ip() -> str:
    import ipaddress
    import socket
    import subprocess
    for exe in ("tailscale", r"C:\Program Files\Tailscale\tailscale.exe"):
        try:
            out = subprocess.run([exe, "ip", "-4"], capture_output=True, text=True, timeout=5,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.split()
            if out:
                return out[0]
        except Exception:
            pass
    try:
        for ai_ in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = ai_[4][0]
            if ipaddress.ip_address(ip) in ipaddress.ip_network("100.64.0.0/10"):
                return ip
    except Exception:
        pass
    return ""

def ai_settings() -> dict:
    try:
        import ai_check
        return ai_check.load_settings()
    except Exception:
        return {}


__all__ = [n for n in dir() if not n.startswith("__")]   # с «_именами»: модули ниже берут их через *
