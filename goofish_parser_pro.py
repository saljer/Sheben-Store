"""Sheben'Store: сервер приложения (--listen) и командная строка.
Остальное — в модулях gp_parse → gp_score → gp_market → gp_pages → gp_process: каждый берёт всё из предыдущего,
поэтому goofish_parser_pro.X по-прежнему даёт любую функцию (app.pyw, tests/regress.py)."""
from __future__ import annotations

from gp_process import *  # noqa: F401,F403


def listen(a, cat: dict, prices: dict, outdir: Path, db, session_start: str):
    """Принимает от расширения chrome_ext ответы поиска/карточек, которые страница Goofish уже
    получила в вашем обычном браузере, и отдаёт живой отчёт. Сам на сайт ничего не запрашивает."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import parse_qs, urlparse

    lock = threading.Lock()
    state = {"version": 0, "html": "", "rate_note": "", "started": time.time()}
    hosts = {"127.0.0.1:8765", "localhost:8765"}
    phone = {"srv": None, "ip": "", "err": ""}

    def phone_cfg() -> tuple[bool, str]:
        s = ai_settings()
        return bool(s.get("phone_on")), str(s.get("phone_token") or "")

    def phone_bg():
        """Второй адрес — Tailscale: включается/выключается кнопкой на «Статусе», Tailscale может подняться позже."""
        def stop(srv):
            srv.shutdown()
            srv.server_close()
        while True:
            on, tok = phone_cfg()
            if on and tok and phone["srv"] is None:
                ip = tailscale_ip()
                if not ip:
                    phone["err"] = "Tailscale не найден: установите его и войдите в аккаунт"
                else:
                    try:
                        srv = ThreadingHTTPServer((ip, 8765), Handler)
                        hosts.add(f"{ip}:8765")
                        phone.update(srv=srv, ip=ip, err="")
                        threading.Thread(target=srv.serve_forever, daemon=True).start()
                        say(f"Доступ с телефона включён: http://{ip}:8765/")
                    except Exception as e:
                        phone["err"] = "не удалось открыть адрес Tailscale: " + repr(e)[:120]
            elif not (on and tok) and phone["srv"] is not None:
                srv, phone["srv"] = phone["srv"], None
                hosts.discard(f'{phone["ip"]}:8765')
                threading.Thread(target=stop, args=(srv,), daemon=True).start()
                say("Доступ с телефона выключен")
            time.sleep(5)

    def order_matches(good: list[dict]) -> dict:
        out = {}
        for o in load_orders()["items"]:
            if not o["active"]:
                continue
            ms = []
            for r in good:
                fit = order_fit(o, r)
                if fit:
                    pr = profit_calc(r["price_cny"], o["budget"], a, cat, r.get("ckey") or "?", r.get("kg") or None, r.get("site", ""))["profit_rub"]
                    if pr > 0:
                        ms.append({"id": r["id"], "fit": fit, "profit": pr, "price": r["price_cny"], "size": r.get("size", ""),
                                   "cost": round(r.get("cost_rub") or 0), "late": order_late(o, r),
                                   "title": f'{r.get("brand", "")} · {r.get("model") or r.get("category", "")}',
                                   "url": r.get("url", ""), "image": r.get("image", "")})
            ms.sort(key=lambda m: (m["fit"] != "да", -m["profit"]))
            out[o["id"]] = ms
        return out

    def morning_summary(since: str):
        """Утренняя сводка: что нового с прошлой сводки (в первый раз — за сутки). Вызывается под lock."""
        fmt = lambda v: f"{v:,.0f}".replace(",", " ")
        since = since or (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds")
        by = state.get("by_id", {})
        new_ids = [i for (i,) in db.execute("SELECT id FROM items WHERE first_seen >= ?", (since,)) if i in by]
        drops = [i for (i,) in db.execute("SELECT id FROM items WHERE price_changed >= ? AND price < prev_price", (since,)) if i in by]
        new = [by[i] for i in new_ids]
        best = sorted({r["id"]: r for r in new + [by[i] for i in drops]
                       if isinstance(r.get("profit_rub"), int) and r["profit_rub"] > 0}.values(), key=lambda r: -r["profit_rub"])[:3]
        nid = set(new_ids)
        orders_new = sum(1 for ms in state.get("order_matches", {}).values() for m in ms if m["id"] in nid and m["fit"] == "да")
        text = (f"Новых в ленте: {len(new)} (уровня A: {sum(r.get('tier') == 'A' for r in new)}) · подешевело: {len(drops)}"
                + (f" · по заказам: {orders_new}" if orders_new else "")
                + ("\nЛучшее: " + "; ".join(f'{r["brand"]} {r.get("model") or ""} ¥{fmt(r["price_cny"])} +{fmt(r["profit_rub"])} ₽'.replace("  ", " ")
                                            for r in best) if best else ""))
        add_alert("summary", {}, text=text)
        save_settings_keys(summary_last=datetime.now().isoformat(timespec="seconds"))
        say("Утренняя сводка:", text.replace("\n", " · "))
    load_costs()
    a.rate = eff_rate()
    refresh_own_sales(load_deals(db))
    try:        # секреты живут в .env: то, что вписано в settings.json (в том числе руками), переезжает туда при запуске
        import ai_check
        import config
        moved = config.migrate_secrets(ai_check.SETTINGS)
        if moved:
            say("Секреты перенесены из settings.json в .env:", ", ".join(moved))
        raw = json.loads(ai_check.SETTINGS.read_text(encoding="utf-8")) if ai_check.SETTINGS.exists() else {}
        if "tg_min_profit" not in raw:
            save_settings_keys(_help=ai_check.DEFAULTS["_help"], tg_min_profit=ai_check.DEFAULTS["tg_min_profit"])
    except Exception as e:
        say("settings.json / .env: не удалось подготовить настройки:", repr(e)[:120])

    def rates_bg():     # курс — в фоне, чтобы окно не ждало сети; приложение неделями открыто — проверяем раз в час
        while True:
            note = update_rates()
            with lock:
                state["rate_note"] = note
                if not note and abs(eff_rate() - a.rate) > 1e-6:
                    a.rate = eff_rate()
                    _EVAL_CACHE.clear()
                    rebuild()
            time.sleep(3600)

    def phash_bg():
        """Отпечатки фото ленты для поиска похожих снимков. Миниатюры берём с CDN картинок alicdn (их же грузит лента),
        по одной в секунду, лучшие вещи первыми; сам сайт Goofish не трогаем."""
        import io
        import urllib.request
        try:
            from PIL import Image
        except ImportError:
            say("Похожие фото: нет библиотеки Pillow, проверка выключена")
            return
        done, fails = 0, {}
        while True:
            with lock:
                todo = [r for r in state.get("by_id", {}).values()     # бывает не ссылка, а «1280x720» — пропускаем
                        if re.match(r"(https?:)?//", r.get("image") or "") and r["id"] not in PHASH and fails.get(r["id"], 0) < 3]
            if not todo:
                if done:
                    state["dirty_new"] = True      # пересчёт сделает lazy_bg — один общий, а не свой у каждой фоновой задачи
                    say(f"Похожие фото: отпечатков {len(PHASH)}")
                    done = 0
                time.sleep(30)
                continue
            r = todo[0]
            u = r["image"]
            u = "https:" + u if u.startswith("//") else re.sub(r"^http:", "https:", u)
            if "alicdn.com" in u and not re.search(r"_\d+x\d+", u):
                u += "_200x200.jpg"
            try:
                req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    h = dhash(Image.open(io.BytesIO(resp.read(3_000_000))))
                with lock:
                    db.execute("INSERT OR REPLACE INTO phash VALUES(?,?,?)",
                               (r["id"], f"{h:016x}", datetime.now().isoformat(timespec="seconds")))
                    db.commit()
                    PHASH[r["id"]] = h
                done += 1
            except Exception as e:
                fails[r["id"]] = fails.get(r["id"], 0) + 1
                if len(fails) <= 3:
                    say("Похожие фото: миниатюра не скачалась:", repr(e)[:120])
            if done >= 150:     # пересчёт ленты пачками, а не после каждой картинки
                state["dirty_new"] = True      # через lazy_bg (см. выше)
                done = 0
            time.sleep(1)

    basket_file = outdir / "basket.json"
    try:
        state["basket"] = [str(i) for i in json.loads(basket_file.read_text(encoding="utf-8"))][:30]
    except Exception:
        state["basket"] = []

    def basket_view() -> dict:
        """Корзина Японии для вкладки «Сделки»: вещи (те, что ещё в ленте) и расчёт посылки."""
        by = state.get("by_id", {})
        rows = [by[i] for i in state.get("basket", []) if i in by]
        thumb = lambda u: ("https:" + u if u.startswith("//") else u)
        return {"items": [{"id": r["id"], "name": f'{r.get("brand", "")} · {r.get("model") or r.get("category", "")}', "title": r.get("title", ""),
                           "url": r.get("url", ""), "img": thumb(r.get("image", "")), "yen": r.get("price_jpy") or 0,
                           "p": r.get("profit_rub") if isinstance(r.get("profit_rub"), int) else None} for r in rows],
                "calc": basket_calc(rows), "gone": len(state.get("basket", [])) - len(rows)}

    def record_sold(rows: list[dict], now: str) -> int:
        """Проданные лоты Mercari -> таблица sold: цена сделки, состояние и за сколько дней ушло (от публикации).
        Бренд, модель и тип определяем теми же правилами, что в ленте. Проданная вещь из ленты уходит в архив."""
        n = 0
        for r in rows:
            if db.execute("SELECT 1 FROM sold WHERE id=?", (r["id"],)).fetchone():
                continue
            title, desc = low(r.get("title", "")), low(r.get("labels", ""))
            bkey, _ = detect_brand(title, title + " | " + desc, cat)
            b = cat["brands"].get(bkey) if bkey else None
            model = detect_model(title + " | " + desc, b, title) if b else None
            ckey = detect_category(title, desc, cat)
            try:
                days = round((datetime.fromisoformat(now) - datetime.fromisoformat(r["posted"])).total_seconds() / 86400, 2) if r.get("posted") else None
            except ValueError:
                days = None
            db.execute("INSERT INTO sold(id, site, title, brand, model, category, cond, price, posted, seen, days) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       (r["id"], "mercari", r.get("title", "")[:200], b["name"] if b else "", (model or {}).get("name", ""),
                        cat["categories"].get(ckey, {}).get("name", "") if ckey else "", r.get("jp_cond", ""), r.get("price_jpy") or 0,
                        r.get("posted", ""), now, days))
            db.execute("UPDATE items SET arch = 3 WHERE id = ? AND arch = 0 AND COALESCE(mark, '') = ''", (r["id"],))
            n += 1
        if n:
            db.commit()
        return n

    def deals_post(path: str, body: dict) -> dict:
        """«Купил» в ленте, правки на вкладке «Сделки», ставки калькулятора. Вызывается под lock."""
        now = datetime.now().isoformat(timespec="seconds")
        if path == "/deal":
            iid = str(body.get("id", ""))
            r = state.get("by_id", {}).get(iid)
            if r is None:
                raise ValueError("вещь не найдена в ленте — обновите страницу")
            if db.execute("SELECT 1 FROM deals WHERE item_id=?", (iid,)).fetchone():
                return {"ok": True}
            price = float(body.get("price_cny") or r["price_cny"])
            ckey = r.get("ckey") or "?"
            sell = r.get("sell_rub") if isinstance(r.get("sell_rub"), (int, float)) else None
            kg = r.get("kg") or None
            calc = profit_calc(price, sell, a, cat, ckey, kg) if sell else {"cost_rub": round(sum(v for _, v in cost_parts(price, ckey, kg)))}
            data = {k: r.get(k, "") for k in ("title", "brand", "model", "category", "image", "url", "seller")}
            data.update(ckey=ckey, price_cny=price, rate=round(eff_rate(), 4), cost_rub=calc["cost_rub"],
                        sell_rub=sell, profit_rub=calc.get("profit_rub"), channel="", site=r.get("site", ""),
                        flags={k: r.get(k, "") for k in RISK_FLAGS})
            db.execute("INSERT INTO deals(item_id, data, status, ts_buy) VALUES(?,?,?,?)",
                       (iid, json.dumps(data, ensure_ascii=False), DEAL_STATUSES[0], now))
            db.commit()
            say(f"Сделка: куплено «{data['title'][:40]}» за ¥{price:.0f}")
            rebuild()
            return {"ok": True}
        if path == "/deal/manual":      # покупка мимо кнопки «Купил»: ссылка или бренд + цена
            url, site = str(body.get("url") or "").strip()[:300], ("mercari" if body.get("site") == "mercari" else "")
            m = re.search(r"[?&]id=(\d+)", url) or re.search(r"/(?:item|product)/(\w+)", url)
            iid = m.group(1) if m else ""
            known = db.execute("SELECT data FROM items WHERE id=?", (iid,)).fetchone() if iid else None
            r = state.get("by_id", {}).get(iid) or (json.loads(known[0]) if known else {})
            if r.get("site") == "mercari":
                site = "mercari"
            price_in = max(float(body.get("price") or 0), 0)
            if not price_in:
                raise ValueError("нужна цена покупки")
            price = jpy_to_cny(price_in) if site == "mercari" else price_in     # сделки хранят юани (Mercari — эквивалент)
            ckey = r.get("ckey") or (str(body.get("ckey")) if body.get("ckey") in COSTS["weights"] else "?")
            sell = float(body["sell"]) if body.get("sell") else (r.get("sell_rub") if isinstance(r.get("sell_rub"), (int, float)) else None)
            kg = r.get("kg") or None
            calc = (profit_calc(price, sell, a, cat, ckey, kg, site) if sell
                    else {"cost_rub": round(sum(v for _, v in cost_parts(price, ckey, kg, site)))})
            data = {"title": r.get("title") or (url if not url.startswith("http") else ""), "brand": r.get("brand") or str(body.get("brand") or "")[:40],
                    "model": r.get("model", ""), "category": r.get("category") or cat["categories"].get(ckey, {}).get("name", ""),
                    "image": r.get("image", ""), "url": url if url.startswith("http") else r.get("url", ""), "seller": r.get("seller", ""),
                    "ckey": ckey, "price_cny": price, "rate": round(eff_rate(), 4), "cost_rub": calc["cost_rub"], "sell_rub": sell,
                    "profit_rub": calc.get("profit_rub"), "channel": "", "site": site, "manual": True,
                    "flags": {k: r.get(k, "") for k in RISK_FLAGS} if r else {}}
            db.execute("INSERT INTO deals(item_id, data, status, ts_buy) VALUES(?,?,?,?)",
                       (iid or f"manual:{int(time.time())}", json.dumps(data, ensure_ascii=False), DEAL_STATUSES[0], now))
            db.commit()
            say(f"Сделка вручную: «{(data['title'] or data['brand'])[:40]}» за ¥{price_in:.0f}{' JP' if site else ''}")
            rebuild()
            return {"ok": True}
        if path == "/market/manual":    # цена России, которую вы видели сами: в ту же таблицу, что посты каналов и Avito
            what, where = str(body.get("what") or "").strip()[:120], str(body.get("where") or "avito")[:20]
            price = int(float(body.get("price") or 0))
            if not what or price < 1500:
                raise ValueError("нужны бренд/модель и цена от 1 500 ₽")
            text = (f"{what}\n{str(body.get('cond') or '')[:60]}\nЦена: {price} ₽"
                    + (f"\nвисит {int(float(body.get('days') or 0))} дн." if body.get("days") else "") + ("\nпродано" if body.get("sold") else ""))
            db.execute("INSERT OR REPLACE INTO market(key, source, url, ts, seen, text) VALUES(?,?,?,?,?,?)",
                       (f"manual:{now}", f"shop:{where}", "", now, now, text))
            db.commit()
            p = parse_market_post(text, cat, shop=True)
            say(f"Цена России вручную: {what} — {price} ₽ ({where})" + ("" if p else " — бренд не распознан"))
            rebuild()
            return {"ok": True, "parsed": f'{cat["brands"][p["brand"]]["name"]} {p.get("model") or ""} — {price} ₽'.replace("  ", " ") if p else ""}
        if path == "/basket":           # корзина Японии: вещи Mercari для одной посылки
            ids = [i for i in state.get("basket", []) if i != str(body.get("id"))]
            if body.get("on") and body.get("id"):
                ids.append(str(body["id"]))
            state["basket"] = ids[-30:]
            basket_file.write_text(json.dumps(state["basket"]), encoding="utf-8")
            return basket_view()
        if path == "/deal/update":
            did = int(body.get("deal_id", 0))
            row = db.execute("SELECT data, status, ts_sold FROM deals WHERE id=?", (did,)).fetchone()
            if row is None:
                raise ValueError("сделка не найдена")
            if body.get("delete"):
                db.execute("DELETE FROM deals WHERE id=?", (did,))
            else:
                data, status, ts_sold = json.loads(row[0]), row[1], row[2]
                if body.get("status") in DEAL_STATUSES:
                    status = body["status"]
                    ts_sold = (ts_sold or now) if status == "продано" else None
                for k in ("cost_real", "sold_rub", "sold_fee"):
                    if k in body:
                        v = body[k]
                        data[k] = None if v in (None, "") else max(float(v), 0)
                if "channel" in body:
                    data["channel"] = str(body["channel"])[:40]
                if "expert" in body:        # журнал экспертиз: результат проверки подлинности
                    data["expert"] = body["expert"] if body["expert"] in EXPERT_RESULTS else ""
                    data["expert_ts"] = now if data["expert"] else ""
                if data.get("sold_rub") and status != "продано":     # вписали цену продажи — значит продано
                    status, ts_sold = "продано", ts_sold or now
                db.execute("UPDATE deals SET data=?, status=?, ts_sold=? WHERE id=?",
                           (json.dumps(data, ensure_ascii=False), status, ts_sold, did))
            db.commit()
            if refresh_own_sales(load_deals(db)):
                _EVAL_CACHE.clear()
                unarchive_recent(db)
            rebuild()
            return {"ok": True}
        # /costs
        if body.get("refresh_rate"):
            COSTS["rate_auto"] = True
            state["rate_note"] = update_rates(force=True)
        else:
            auto = bool(body.get("rate_auto", COSTS.get("rate_auto")))
            paid = False
            for k, v in body.items():
                if k == "rate_auto":
                    COSTS[k] = bool(v)
                elif k == "weights" and isinstance(v, dict):
                    COSTS["weights"].update({str(w): max(float(x), 0) for w, x in v.items()})
                elif k in ("rate", "eur_rate", "jpy_rate", "usd_rate") and auto:
                    continue        # курс берётся сам — в поле лишь показан, старое значение со страницы не записываем
                elif k == "pay_rate":
                    pay = max(float(v), 0)
                    if pay and abs(pay - COSTS.get("pay_rate", 0)) > 1e-6:
                        set_pay_rate(pay)       # наценка к курсу ЦБ/рынка — из курса KwikPay
                        paid = True
                elif k in COST_DEFAULTS and k not in ("rate_date", "pay_date", "rate_src", "weights", "rate_markup", "jp_profile"):
                    COSTS[k] = max(float(v), 0)
            # наценку поменяли руками (поле показывает 3 знака процента) — курс KwikPay больше не в силе
            if not paid and "rate_markup" in body and abs(float(body["rate_markup"]) - COSTS["rate_markup"]) > 2e-5:
                COSTS["rate_markup"] = float(body["rate_markup"])
                COSTS["pay_rate"] = 0.0
            save_costs()
        a.rate = eff_rate()
        _EVAL_CACHE.clear()
        unarchive_recent(db)
        rebuild()
        return {"ok": True, "costs": COSTS}

    def rebuild():
        state.update(dirty=False, dirty_new=False, built_at=time.time())
        if refresh_market_sell(db, cat):     # цены продажи от рынка России изменились — вещи переоцениваются
            _EVAL_CACHE.clear()
            unarchive_recent(db)
        rows = load_rows(db, session_start)
        good, bad, reasons = process(rows, a, cat, prices, outdir, refresh=a.html_refresh)
        deal_ids = {iid for (iid,) in db.execute("SELECT item_id FROM deals")}
        if "snapped" not in state:      # бэктест: первый взгляд Щебня на вещь записывается один раз
            state["snapped"] = {i for (i,) in db.execute("SELECT id FROM snap")}
        snap_rows(db, good, state["snapped"], hashlib.md5(json.dumps(cat, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:8])
        for r in good:
            r["deal"] = 1 if r["id"] in deal_ids else ""
        state["version"] += 1
        midx = market_index(db, cat)      # цены России (каналы, Avito, Oskelly) — строкой в карточке и на панели
        for r in good:
            r.update(ru_mkt="", ru_src="", ru_by="", ru_diff="", sq=similar_query(r, cat, state.get("qstats", {})))   # sq — кнопка «Похожее»
            r.update(market_note(r, cat, midx))
            r["gender"] = gender_of(r)      # мужское / женское — пометка и фильтр, оценку не меняет
        state["like_sim"] = like_similar(good, cat)     # «Похоже на ваши ★» на «Главной»
        state["mkt_gaps"] = market_gaps(good, cat)      # «Не хватает цен России» на «Главной»
        state["leads"] = market_leads(db, cat=cat)      # «Ищут»: посты «ищу / куплю» из каналов и чатов
        # цены настоящих сделок Mercari (таблица sold) за 4 месяца: по модели, иначе по бренду и типу вещи; от 3 продаж
        sold: dict = {}
        for b_, m_, c_, p_, d_ in db.execute("SELECT brand, model, category, price, days FROM sold WHERE brand != '' AND seen >= ?",
                                               ((datetime.now() - timedelta(days=120)).isoformat(),)):
            for k in ((b_, "m", m_),) if m_ else ():
                sold.setdefault(k, []).append((p_, d_))
            sold.setdefault((b_, "c", c_), []).append((p_, d_))
        fy = lambda v: f"{v:,.0f}".replace(",", " ")
        for r in good:
            r["jp_sold"] = ""
            if r.get("site") == "mercari":
                v = (r.get("model") and sold.get((r["brand"], "m", r["model"]))) or sold.get((r["brand"], "c", r["category"]))
                if v and len(v) >= 3:
                    ds = [d for _, d in v if d is not None]
                    r["jp_sold"] = (f"¥{fy(statistics.median(p for p, _ in v))} · {len(v)} продаж"
                                    + (f" · обычно за {statistics.median(ds):.0f} дн." if len(ds) >= 3 else ""))
        state["sold_n"] = db.execute("SELECT COUNT(*) FROM sold").fetchone()[0]
        state["by_id"] = {r["id"]: r for r in good}
        med: dict = {}      # обычная цена бренда и типа вещи по площадке — для фильтра на входе (premod)
        for r in good:
            if r.get("tier") == "A" and r.get("price_cny"):
                med.setdefault((r["brand"], r["category"], r.get("site", "")), []).append(r["price_cny"])
        PRE_MED.clear()
        PRE_MED.update({k: statistics.median(v) for k, v in med.items() if len(v) >= 6})
        state["feed"] = good        # лента порциями (/feed): список уже по оценке; заменяется целиком — читать можно без замка
        state["rejects"] = {r["id"]: r.get("reject", "") for r in bad}     # панель на странице товара: почему отсеяна
        # шифрованный id продавца из поиска -> числовой (из открытых карточек): для «Моих продавцов» на «Главной»
        state["seller_map"] = {enc: uid for uid, enc in db.execute("SELECT uid, enc FROM sellers WHERE enc != ''")}
        state["market_hot"] = market_hot(db, cat)      # «Ходовое в каналах» на «Главной» (пересчёт только при новых постах)
        state["home_top"] = [r for r in good if r.get("tier") == "A" and not r.get("outside")][:8]
        state["home_new"] = [r for r in good if r.get("status")][:8]
        pk = load_picks()       # «Подборка» на «Главной»: вещи, которые просили найти
        if pk["ids"]:
            gb = {r["id"]: r for r in good}
            state["picks"] = {"title": pk["title"], "budget": pk["budget"],
                              "rows": [(gb[i], pk["notes"].get(i, "")) for i in pk["ids"] if i in gb]}
        else:
            state["picks"] = {}
        state["best_profit"] = max((r["profit_rub"] for r in good if isinstance(r.get("profit_rub"), int)), default=0)
        state["pulse"] = pulse_data(db, state)     # «Пульс» на «Главной»
        qst: dict = {}      # сколько толку от каждого запроса: увидено / прошло в ленту / лучшая прибыль
        for r in rows:
            for q in str(r.get("queries", "")).split("; "):
                if q:
                    s = qst.setdefault(skey(q, r.get("site", "")), [0, 0, 0])
                    s[0] += 1
                    g = state["by_id"].get(r["id"])
                    if g:
                        s[1] += 1
                        s[2] = max(s[2], g["profit_rub"] if isinstance(g.get("profit_rub"), int) else 0)
        for k, seen, ok, best in db.execute("SELECT key, seen, good, best FROM qarch"):     # вещи, ушедшие в архив
            s = qst.setdefault(k, [0, 0, 0])
            s[0], s[1], s[2] = s[0] + seen, s[1] + ok, max(s[2], best)
        state["qstats"] = qst
        ar = archive_sweep(db, good, bad, cat, deal_ids)
        if ar["stale"] or ar["junk"]:
            say(f"  в архив: {ar['junk']} отсеянных, {ar['stale']} давно не попадались; в работе было {len(rows)}")
        ck = state.get("checker")
        if ck is not None:      # очередь ИИ: сначала «тёмные лошадки» (бренд/тип не ясен), потом сильные вещи ленты
            mp = float(ai_settings().get("ai_min_price", 500))
            ok_ = lambda r: r.get("image") and not r.get("ai") and r.get("mark") != "hide" and r["price_cny"] >= mp
            def fashion(r):     # «тёмная лошадка» — только то, что похоже на одежду/обувь/сумку, а не объективы и ноутбуки
                t = low(f'{r.get("title", "")} {r.get("desc", "")} {r.get("labels", "")}')
                if has_any(t, NON_FASHION):
                    return False
                ck_ = detect_category(low(r.get("title", "")), low(r.get("desc", "") + " " + r.get("labels", "")), cat)
                return bool(ck_ and cat["categories"][ck_].get("enabled", True)) or "品牌:" in t
            dark = [r for r in bad if ok_(r) and r.get("reject") in ("бренд не из каталога", "тип вещи не распознан") and fashion(r)]
            dark.sort(key=lambda r: r.get("last_seen", ""), reverse=True)
            unclear = [r for r in good if ok_(r) and (str(r.get("category", "")).startswith("Тип") or r.get("outside"))]
            top = sorted((r for r in good if ok_(r) and r.get("total", 0) >= 70), key=lambda r: -r["total"])
            seen_q, q = set(), []
            for r in dark[:150] + unclear + top[:150]:
                if r["id"] not in seen_q:
                    seen_q.add(r["id"])
                    q.append(r)
            ck.set_queue(q)
        om = state["order_matches"] = order_matches(good)
        ords = {o["id"]: {"client": o["client"], "what": " ".join(x for x in (o["brand"], o["model"], o["words"], o["size"]) if x),
                          "budget": o["budget"], "ids": {m["id"]: m["profit"] for m in om.get(o["id"], [])}}
                for o in load_orders()["items"] if o["active"]}
        state["order_ids"] = {k: v["ids"] for k, v in ords.items()}
        state["html"] = render_html(good, bad, reasons, a.rate, live=True, version=state["version"],
                                    extra={"costs": COSTS, "channels": cat.get("channels") or [], "tax": a.tax, "orders": ords,
                                           "pre": state.get("premod") or {}, "basket": state.get("basket", [])})
        n_new = sum(1 for r in rows if r["status"] == "новое")
        n_top = sum(1 for r in good if r["total"] >= 75)
        state.update(new=n_new, new_good=sum(1 for r in good if r["status"] == "новое"),
                     total=len(rows), good=len(good), top=n_top)
        say(f"  в базе: {len(rows)} (новых за сеанс: {n_new}); прошло фильтры: {len(good)}; оценка 75+: {n_top}")
        state["built_end"] = time.time()     # конец пересчёта: от него lazy_bg отсчитывает паузу до следующего

    # --- поиск в двух вариантах (иероглифы / латиница): автопоиск чередует их, здесь копим итоги и выбираем лучший
    trials_file = outdir / "search_trials.json"
    TRIAL_RUNS = 25         # проходов каждого варианта для выбора; или 2 дня, если у обоих хотя бы 5
    try:
        state["trials"] = json.loads(trials_file.read_text(encoding="utf-8"))
    except Exception:
        state["trials"] = {}

    def trial_note(q: str, n: int, good: int, new: int):
        s = load_searches()
        it = next((x for x in s["items"] if x.get("alt") and not x.get("picked") and q in (x["q"], x["alt"])), None)
        if not it:
            return
        key = it["q"]       # ключ — основной запрос строки «Моих поисков»
        t = state["trials"].setdefault(key, {"since": datetime.now().isoformat(timespec="seconds")})
        v = t.setdefault(q, [0, 0, 0, 0])      # проходов, объявлений, из них в ленте, новых
        for i, d in enumerate((1, n, good, new)):
            v[i] += d
        a, b = t.get(it["q"]), t.get(it["alt"])
        ripe = a and b and (min(a[0], b[0]) >= TRIAL_RUNS or (
            min(a[0], b[0]) >= 5 and t["since"] < (datetime.now() - timedelta(days=2)).isoformat()))
        if ripe:
            sc = lambda v: (v[2] + 2 * v[3]) / max(v[0], 1)       # в ленту + новые (вдвойне) за проход
            win, lose = (it["q"], it["alt"]) if sc(a) >= sc(b) else (it["alt"], it["q"])
            w, l = t[win], t[lose]
            lab = lambda x: "中" if re.search(r"[一-鿿]", x) else "EN"
            why = (f"{lab(win)}: за проход в ленту {w[2] / w[0]:.1f}, новых {w[3] / w[0]:.1f} — "
                   f"у «{lose}» {l[2] / l[0]:.1f} и {l[3] / l[0]:.1f}")
            it.update(q=win, alt=lose, picked=True, why=why)
            save_searches(s)
            state["trials"].pop(key, None)
            say(f"Поиск: «{win}» лучше, чем «{lose}» ({why})")
        trials_file.write_text(json.dumps(state["trials"], ensure_ascii=False), encoding="utf-8")

    # --- раз в день и после нового сбора каналов: «ходовое в каналах» -> «Мои поиски» с пометкой «из каналов»
    def chan_update():
        today = date.today().isoformat()
        with lock:
            mh = market_hot(db, cat)
            hot = [r for r in mh if r["n"] >= CHAN_MIN_POSTS and not r["in_cat"]][:CHAN_MAX]
            # для Mercari — по брендам (и каталожные: своих поисков Mercari по ним нет), посты моделей бренда складываем
            by_b: dict = {}
            for r in mh:
                g = by_b.setdefault(r["brand"], dict(r, n=0))
                g["n"] += r["n"]
            hot_jp = [r for r in sorted(by_b.values(), key=lambda r: -r["n"]) if r["n"] >= CHAN_MIN_POSTS]
            state["chan_key"] = db.execute("SELECT COUNT(*), MAX(seen) FROM market").fetchone()
        cn_map = load_brand_cn()
        asked = []
        ck = state.get("checker")
        for r in hot:       # китайского названия нет ни в brand_cn.json, ни в каталоге — один вопрос Gemini
            if brand_variants(r, cat, cn_map["brands"])[1] is None and ck is not None:
                try:
                    cn_map["brands"][r["brand"]] = ck.brand_cn(r["brand"])
                    cn_map["_gemini"].append(r["brand"])
                    asked.append(f'{r["brand"]} → {cn_map["brands"][r["brand"]] or "латиницей"}')
                except Exception as e:
                    say(f"Поиски из каналов: Gemini не ответил про {r['brand']}: {repr(e)[:120]}")
        if asked:
            save_brand_cn(cn_map)
            say("Китайские названия от Gemini:", "; ".join(asked))
        with lock:
            s = load_searches()
            items, qst = s["items"], state.get("qstats", {})
            idx = {low(v): x for x in items if x.get("site") != "mercari" for v in (x["q"], x.get("alt", "")) if v}
            added, paired, removed = [], [], []
            # «мало толку»: такой поиск из каналов убирается — и заново в том же проходе не добавляется
            is_junk = lambda x: (query_junk(qst, skey(x["q"], x.get("site", "")))
                                 and (not x.get("alt") or query_junk(qst, x["alt"])))
            for r in hot:
                lat, cn = brand_variants(r, cat, cn_map["brands"])
                cn = cn or ""
                hit = idx.get(lat) or (idx.get(low(cn)) if cn else None)
                if hit:
                    if hit.get("src") == "каналы":
                        hit["hot_day"] = today
                    other = cn if low(hit["q"]) == lat else lat if cn and low(hit["q"]) == low(cn) else ""
                    if other and not hit.get("alt") and not hit.get("seller") and low(other) not in idx:
                        hit["alt"] = other      # ваш поиск латиницей — добавляем иероглифы, автопоиск сравнит
                        idx[low(other)] = hit
                        paired.append(hit["q"])
                    continue
                if (sum(1 for x in items if x.get("src") == "каналы") >= CHAN_MAX
                        or sum(1 for x in items if not x.get("seller")) >= SEARCH_MAX):
                    continue
                new = {"q": cn or lat, "src": "каналы", "hot_day": today} | ({"alt": lat} if cn else {})
                if is_junk(new):
                    continue
                items.append(new)
                idx[low(new["q"])] = new
                added.append(new["q"])
            # те же бренды — поиском на Mercari (латиницей; поиск Mercari сам сортирует «сначала новые»)
            jp_idx = {low(x["q"]): x for x in items if x.get("site") == "mercari" and not x.get("seller")}
            for r in hot_jp:
                lat = brand_variants(r, cat, cn_map["brands"])[0]
                if not re.search(r"[a-z]", lat) or re.search(r"[一-鿿]", lat):
                    continue
                hit = jp_idx.get(lat)
                if hit:
                    if hit.get("src") == "каналы":
                        hit["hot_day"] = today
                    continue
                if (sum(1 for x in items if x.get("src") == "каналы" and x.get("site") == "mercari") >= CHAN_MAX_JP
                        or sum(1 for x in items if not x.get("seller")) >= SEARCH_MAX):
                    continue
                new = {"q": lat, "site": "mercari", "src": "каналы", "hot_day": today}
                if is_junk(new):
                    continue
                items.append(new)
                jp_idx[lat] = new
                added.append(f"{lat} (Mercari)")
            week = (date.today() - timedelta(days=7)).isoformat()
            for x in list(items):   # свои поиски не трогаем; «из каналов» — неделю не в ходовом или «мало толку»
                if x.get("src") == "каналы" and (x.get("hot_day", today) < week or is_junk(x)):
                    items.remove(x)
                    removed.append(x["q"] + (" (Mercari)" if x.get("site") == "mercari" else ""))
            if added or removed or paired:
                save_searches(s)
            state["chan_day"] = today
            if added or removed or paired:
                text = "; ".join(p for p in (f"добавлены: {', '.join(added)}" if added else "",
                                             f"убраны: {', '.join(removed)}" if removed else "",
                                             f"китайский вариант для: {', '.join(paired)}" if paired else "") if p)
                say("Поиски из каналов:", text)
                add_alert("info", {}, text=f"Поиски из каналов: +{len(added)}, −{len(removed)}. {text}")

    # --- ★ -> «Мои поиски» с пометкой «из избранного»: Gemini один раз смотрит фото вещи и пишет запрос (как «Похожее»).
    # Одинаковые запросы склеиваются; не больше FAV_MAX поисков — по последним ★; автопоиск заходит в них раз в 3 круга
    fav_file = outdir / "fav_queries.json"
    FAV_MAX = 10
    fav_norm = lambda q: " ".join(sorted(low(q).split()))

    def fav_update():
        with lock:
            liked = db.execute("SELECT i.id, i.data, MAX(l.ts) AS t FROM items i LEFT JOIN marks_log l ON l.id = i.id "
                               "AND l.mark = 'like' WHERE i.mark = 'like' GROUP BY i.id ORDER BY t DESC").fetchall()
        key = tuple(i for i, _, _ in liked)
        ck = state.get("checker")
        if (key == state.get("fav_key") and state.get("fav_day") == date.today().isoformat())                 or ck is None or not ai_settings().get("gemini_api_key"):
            return
        try:
            cache = json.loads(fav_file.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
        qs, made = cache.setdefault("q", {}), cache.setdefault("made", {})   # запрос по каждой ★; что мы добавляли
        by_id, qst = state.get("by_id", {}), state.get("qstats", {})
        groups: dict = {}       # склеенный запрос -> {q, ids}
        asked, failed, more = 0, False, False
        for iid, raw, _ in liked:       # от последних ★ к старым
            if iid not in qs:
                if len(groups) >= FAV_MAX:
                    continue
                if asked >= 15:     # за раз не больше 15 фото — остальные через 10 минут
                    more = True
                    continue
                r = by_id.get(iid) or json.loads(raw)
                if not r.get("image"):
                    continue
                try:
                    q = ck.photo_query(r, hint=similar_query(r, cat, qst) if r.get("brand") else "")
                    asked += 1
                except Exception as e:
                    say(f"Поиски из избранного: Gemini не ответил про «{r.get('title', '')[:30]}»: {repr(e)[:120]}")
                    failed = True
                    break
                if not q:
                    continue
                qs[iid] = q
            k = fav_norm(qs[iid])
            if k in groups:
                groups[k]["ids"].append(iid)
            elif len(groups) < FAV_MAX:
                groups[k] = {"q": qs[iid], "ids": [iid]}
        today = date.today().isoformat()
        week = (date.today() - timedelta(days=7)).isoformat()
        likes = set(key)
        with lock:
            s = load_searches()
            items = s["items"]
            mine = {low(v) for x in items if x.get("src") != "избранное" for v in (x["q"], x.get("alt", "")) if v}
            added, removed = [], []
            for x in [x for x in items if x.get("src") == "избранное"]:
                k = fav_norm(x["q"])
                junk = query_junk(qst, x["q"]) and x.get("day", today) < week
                pushed = k not in groups and not failed     # Gemini не ответил — список неполный, лишнего не убираем
                if junk or pushed or not likes & set(x.get("fav", [])):
                    items.remove(x)
                    removed.append(x["q"])
                    if not junk:        # ★ сняли или их вытеснили новые — при новой ★ запрос может вернуться
                        made.pop(k, None)
                elif k in groups:
                    x["fav"] = groups[k]["ids"]
            have = {fav_norm(x["q"]) for x in items if x.get("src") == "избранное"}
            for k, gr in groups.items():
                # убранный вами (✕) или за «мало толку» не возвращаем; ваш поиск с тем же запросом уже есть — тоже
                if k in have or k in made or low(gr["q"]) in mine:
                    continue
                if sum(1 for x in items if not x.get("seller")) >= SEARCH_MAX:
                    break
                items.append({"q": gr["q"], "src": "избранное", "rare": True, "fav": gr["ids"], "day": today})
                made[k] = today
                added.append(gr["q"])
            if added or removed or any(x.get("src") == "избранное" for x in items):
                save_searches(s)
            fav_file.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
            if not failed and not more:
                state.update(fav_key=key, fav_day=today)
            if added or removed:
                text = "; ".join(p for p in (f"добавлены: {', '.join(added)}" if added else "",
                                             f"убраны: {', '.join(removed)}" if removed else "") if p)
                say("Поиски из избранного:", text, f"(Gemini посмотрел фото: {asked})")
                add_alert("info", {}, text=f"Поиски из избранного: +{len(added)}, −{len(removed)}. {text}")

    def chan_bg():
        time.sleep(60)
        while True:
            try:
                with lock:
                    key = db.execute("SELECT COUNT(*), MAX(seen) FROM market").fetchone()
                if state.get("chan_day") != date.today().isoformat() or state.get("chan_key") != key:
                    chan_update()
            except Exception as e:
                say("Поиски из каналов: ошибка", repr(e)[:160])
            try:        # новые или снятые ★ — раз в 10 минут (или сразу по «Обновить» у «Моих поисков»)
                fav_update()
            except Exception as e:
                say("Поиски из избранного: ошибка", repr(e)[:160])
            time.sleep(600)

    # --- копия базы раз в день: items.db (вещи, ★, сделки) есть только в одном экземпляре и в git не попадает.
    # Папка backup/ рядом с программой и «OneDrive\ShebenStore backup»: 7 последних дней + по одной на неделю за 4 недели.
    # settings.json (ключи) не берём
    backup_dir = Path(__file__).with_name("backup")
    BACKUP_FILES = ["orders.json", "costs.json", "searches.json", "market.json", "catalog.json", "brand_cn.json",
                    "price_suggestions.json", "out/fav_queries.json", "out/search_trials.json"]

    def backup_now() -> Path:
        import os
        import shutil
        import sqlite3
        import zipfile
        backup_dir.mkdir(exist_ok=True)
        day = date.today().isoformat()
        tmp = backup_dir / f"items-{day}.db.tmp"
        src = sqlite3.connect(db.execute("PRAGMA database_list").fetchone()[2])
        dst = sqlite3.connect(tmp)
        try:
            src.backup(dst)     # целая копия, даже если сервер в это время пишет в базу
            ok = dst.execute("PRAGMA quick_check").fetchone()[0]
            n = dst.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        finally:
            dst.close()
            src.close()
        if ok != "ok":
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"копия базы не прошла проверку: {ok[:80]}")
        out = backup_dir / f"sheben-{day}.zip"
        part = out.with_suffix(".zip.part")
        root = Path(__file__).parent
        with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(tmp, "out/items.db")
            for f in BACKUP_FILES:
                if (root / f).exists():
                    z.write(root / f, f)
        tmp.unlink()
        part.replace(out)
        # хранение: 7 последних + самая свежая копия каждой из 4 недель до них
        olds = sorted(backup_dir.glob("sheben-*.zip"), reverse=True)
        keep, weeks = set(olds[:7]), set()
        for f in olds[7:]:
            wk = date.fromisoformat(f.stem[7:17]).isocalendar()[:2]
            if wk not in weeks and len(weeks) < 4:
                weeks.add(wk)
                keep.add(f)
        for f in olds:
            if f not in keep:
                f.unlink()
        # вторая копия — в OneDrive (переживёт поломку диска); там те же правила хранения
        cloud = ""
        od = os.environ.get("OneDrive") or str(Path.home() / "OneDrive")
        if Path(od).is_dir():
            try:
                cdir = Path(od) / "ShebenStore backup"
                cdir.mkdir(exist_ok=True)
                shutil.copy2(out, cdir / out.name)
                names = {f.name for f in keep}
                for f in cdir.glob("sheben-*.zip"):
                    if f.name not in names:
                        f.unlink()
                cloud = f", копия в OneDrive: {cdir}"
            except Exception as e:
                cloud = f", в OneDrive не скопировано: {repr(e)[:100]}"
        say(f"Копия базы: {out.name} ({out.stat().st_size // 1024 // 1024} МБ, вещей {n}), всего копий: {len(keep)}{cloud}")
        return out

    def backup_bg():
        time.sleep(300)     # не в первые минуты: окно и первый пересчёт ленты важнее
        while True:
            if not (backup_dir / f"sheben-{date.today().isoformat()}.zip").exists():
                try:
                    backup_now()
                    state["backup_err"] = ""
                except Exception as e:
                    say("Копия базы не сделана:", repr(e)[:200])
                    if not state.get("backup_err"):     # одно уведомление, а не каждый час
                        add_alert("info", {}, text=f"Копия базы не сделана: {repr(e)[:160]}")
                    state["backup_err"] = repr(e)[:200]
            time.sleep(3600)

    def searches_refresh():
        for f in (chan_update, fav_update):
            try:
                f()
            except Exception as e:
                say(f"Обновление поисков ({f.__name__}): ошибка", repr(e)[:160])

    def premod_new(items: dict, known) -> int:
        """Фильтр на входе: новые объявления с явным мусором помечаем (row["pre"]) — ingest положит их сразу в архив.
        Возвращает, сколько таких; счётчики по причинам — state["premod"] (строка «Отсеяно» в ленте)."""
        n = 0
        for iid, r in items.items():
            if iid in known:
                continue
            why = premod(r, cat, a.exclude_word)
            if why:
                r["pre"] = why
                run_auth(why)
                n += 1
                pm = state.setdefault("premod", {})
                pm[why.split(":")[0]] = pm.get(why.split(":")[0], 0) + 1
        return n

    def mercari_handle(api_url: str, page_url: str, data: dict) -> dict:
        """Mercari (Япония): выдача, карточка и вещи продавца — так же, как у Goofish: в базу, оценка, уведомления.
        В базе цена в иенах (price_cny = иены), в юань-эквивалент её переводит load_rows."""
        kind, rows, seller, cond = parse_mercari(api_url, data)
        now = datetime.now().isoformat(timespec="seconds")
        if kind == "detail":
            row = rows[0] if rows else None
            iid = row["id"] if row else ""
            changed = False
            if seller:
                db.execute("INSERT INTO sellers(uid, enc, nick, data, ts) VALUES(?,?,?,?,?) ON CONFLICT(uid) DO UPDATE SET "
                           "nick=excluded.nick, data=excluded.data, ts=excluded.ts",
                           (seller["uid"], "", seller["nick"], json.dumps(seller, ensure_ascii=False), now))
                db.commit()
            known = db.execute("SELECT data, price FROM items WHERE id=?", (iid,)).fetchone() if iid else None
            if row and not known:
                ingest(db, {iid: dict(row, queries="карточка")}, now)
                changed = True
            elif row and known and abs(row["price_cny"] - (known[1] or 0)) > 0.009:
                ingest(db, {iid: {"price_cny": row["price_cny"], "price_jpy": row["price_jpy"], "queries": ""}}, now)
                changed = True
            fields = {"desc": row["desc"]} if row and row.get("desc") else {}
            if seller:
                fields.update(seller=seller["nick"], seller_uid=seller["uid"], seller_days=seller["days"],
                              seller_good=seller["good"], seller_bad=seller["bad"], seller_sold=seller["sold"])
            if iid and fields and update_fields(db, iid, **fields):
                changed = True
            if changed:
                say(f"Mercari, карточка {iid}" + (f": продавец «{seller['nick']}», отзывы +{seller['good']}/−{seller['bad']}"
                                                    if seller else ""))
                rebuild()       # панели нужна оценка этой вещи сейчас
            return {"added": 0, "card": card_info(iid, seller)} if iid else {"added": 0}
        if kind not in ("search", "seller"):
            return {"added": 0}
        page_q = parse_qs(urlparse(page_url).query)
        if kind == "search":
            q = str(cond.get("keyword") or (page_q.get("keyword") or ["?"])[0]).strip() or "?"
        m = None
        if kind == "seller":    # вещи продавца: профиль jp.mercari.com/user/profile/<id> или блок «другие вещи» под карточкой
            m = re.search(r"/user/profile/(\d+)", page_url)
            q = "профиль продавца" if m else "профиль продавца (Mercari)"
        n_sold = record_sold([r for r in rows if r.get("sold")], now)
        if n_sold:
            state["dirty"] = True
        gone = sum(1 for r in rows if r.get("sold"))
        rows = [r for r in rows if not r.get("sold")]
        items = {r["id"]: dict(r, queries=q) for r in rows}
        for r in items.values():
            r.pop("sold", None)
        if not items and gone:      # страница «продано» (売り切れ): это не «пустой поиск»
            say(f"Mercari «{q}»: проданных на странице {gone}, новых в базе цен: {n_sold}")
            return {"added": 0, "q": q, "n": gone, "old": gone, "search": False, "sold": n_sold}
        if not items:
            say(f"Mercari «{q}»: ответ без объявлений")
            if kind == "search":
                qe = state.setdefault("qempty", {})
                qe[skey(q, "mercari")] = qe.get(skey(q, "mercari"), 0) + 1
            return {"added": 0, "q": q, "n": 0, "old": 0, "search": kind == "search"}
        seen_before = db.execute(
            f"SELECT id, price, first_seen FROM items WHERE id IN ({','.join('?' * len(items))})", list(items)).fetchall()
        old_prices = {i: p_ for i, p_, _ in seen_before}
        added = len(set(items) - set(old_prices))
        run_add(len(items), set(items) - set(old_prices))
        cut = (datetime.now() - timedelta(minutes=2)).isoformat(timespec="seconds")
        old = sum(1 for _, _, fs in seen_before if fs and fs < cut)
        n_pre = premod_new(items, old_prices)
        ingest(db, items, now)
        fresh = sorted(r["posted"] for r in items.values() if r.get("posted"))
        age = f", самое свежее выложено {fmt_age(fresh[-1])} назад" if fresh and kind == "search" else ""
        pmin = f", от ¥{cond.get('priceMin')}" if kind == "search" and str(cond.get("priceMin") or "0") != "0" else ""
        say(f"Mercari «{q}»: {len(items)} объявлений, из них новых: {added}{age}{pmin}" + (f", мусор на входе: {n_pre}" if n_pre else ""))
        state.setdefault("qempty", {}).pop(skey(q, "mercari"), None)
        if kind == "seller" and m:      # «Мои продавцы» на «Главной»: когда профиль открывали и сколько нового
            state.setdefault("seller_seen", {})[m.group(1)] = {"ts": now, "n": len(items), "added": added}
        qs = state.setdefault("queries", {}).setdefault(q, {"pages": 0, "items": 0, "new": 0})
        qs.update(pages=qs["pages"] + 1, items=qs["items"] + len(items), new=qs["new"] + added,
                  at=datetime.now().strftime("%H:%M"), site="mercari")
        if added > n_pre:
            state.setdefault("qnew_at", {})[skey(q, "mercari")] = time.time()
        moved = any(abs(r["price_cny"] - old_prices[i]) > 0.009 for i, r in items.items() if old_prices.get(i))
        if added > n_pre or moved:      # новый мусор ленту не меняет — пересчёт не нужен
            # цены «до» в базе — иены; уведомления сравнивают юань-эквиваленты
            conv = {i: jpy_to_cny(v) for i, v in old_prices.items()}
            state.setdefault("after_build", []).append((items, conv, q, kind == "search", added))
            state["dirty_new"] = True
        else:
            state["dirty"] = True
        return {"added": added, "q": q, "n": len(items), "old": old, "search": kind == "search",
                "profile": kind == "seller"}

    def card_info(iid: str, seller: dict) -> dict:
        """Для панели на странице товара во вкладке Goofish: оценка из ленты или причина отсева, продавец."""
        r = state.get("by_id", {}).get(iid)
        out = {"id": iid, "seller": seller, "ok": bool(r)}
        if r:
            out.update({k: r.get(k, "") for k in ("total", "tier", "brand", "model", "subtype", "category", "price_cny",
                                                    "sell_rub", "best_channel", "profit_rub", "max_buy_cny", "fake_risk",
                                                    "risk_why", "condition", "deal_pct", "mark", "outside", "verdict_why",
                                                    "ru_mkt", "ru_src", "ru_by", "ru_diff", "site", "price_jpy",
                                                    "max_buy_jpy", "jp_cond")})
        else:
            out["reject"] = state.get("rejects", {}).get(iid, "") or "нет в базе"
            # скрыта вашим ✕: когда и с какой причиной — и кнопка «Вернуть в ленту» на панели
            hid = db.execute("SELECT reason, ts FROM marks_log WHERE id=? AND mark='hide' ORDER BY ts DESC LIMIT 1",
                             (iid,)).fetchone()
            if hid and db.execute("SELECT mark FROM items WHERE id=?", (iid,)).fetchone() == ("hide",):
                d = datetime.fromisoformat(hid[1]).strftime("%d.%m")
                out.update(reject=f"вы скрыли её {d}" + (f" — «{hid[0]}»" if hid[0] else " без причины"), hidden=True,
                           item_only=hid[0] in ITEM_ONLY_REASONS)
        row = db.execute("SELECT data FROM items WHERE id=?", (iid,)).fetchone()
        if seller:
            out.update(seller_flags(seller["uid"], seller["nick"], json.loads(row[0]).get("seller_id", "") if row else ""))
        return out

    def seller_flags(uid: str, nick: str, enc: str = "") -> dict:
        """Продавец реплик? Уже в «Моих продавцах» и как часто его смотрит автопоиск."""
        keys = {uid, enc, "nick:" + nick}
        bl = next((SELLER_BLACKLIST[k] for k in keys if k in SELLER_BLACKLIST), None) or next(
            (s for s in SELLER_BLACKLIST.values() if nick and s.get("seller") == nick), None)
        ent = next((x for x in load_searches()["items"] if x.get("seller") == uid), None)
        return {"blacklist": bl["why"] if bl else "", "followed": bool(ent), "every": bool(ent and not ent.get("rare"))}

    def follow_import(api_url: str, page_url: str, page_q: dict, data: dict) -> dict:
        """Ваши подписки на Goofish -> «Мои продавцы» (раз в 3 круга). Формат ответа сайта сверен не был:
        последний ответ лежит в out/debug_follow.json."""
        api = api_url.split("/h5/")[-1].split("/")[0]
        if "/personal" not in page_url:     # «подписан ли я» на странице товара и т.п. — не список
            return {"added": 0}
        (outdir / "debug_follow.json").write_text(json.dumps({"apiUrl": api_url, "pageUrl": page_url, "data": data},
                                                             ensure_ascii=False)[:400000], encoding="utf-8")
        if "fans" in api_url.lower():
            say(f"  список подписчиков ({api}) — это не подписки, не добавляю")
            return {"added": 0}
        own = (page_q.get("userId") or page_q.get("userid") or [""])[0]     # свой профиль — себя не добавляем
        users = [(u, n) for u, n in follow_users(data) if u != own]
        if len(users) < 2 and "list" not in api.lower():    # кнопка «关注» в шапке чужого профиля, а не список
            return {"added": 0}
        if not users:
            say(f"  подписки ({api}): продавцов в ответе не нашёл, образец — out/debug_follow.json")
            return {"added": 0}
        s = load_searches()
        have = {x.get("seller") for x in s["items"]}
        new = [(u, n) for u, n in users if u not in have]
        now = datetime.now().isoformat(timespec="seconds")
        for u, n in new:
            db.execute("INSERT OR IGNORE INTO sellers(uid, enc, nick, data, ts) VALUES(?,?,?,?,?)",
                       (u, "", n, json.dumps({"uid": u, "nick": n}, ensure_ascii=False), now))
            s["items"].append({"q": f"продавец {n}", "seller": u, "rare": True})
        db.commit()
        s = save_searches(s)
        total = sum(1 for x in s["items"] if x.get("seller"))
        say(f"Подписки ({api}): в ответе {len(users)}, добавлено в «Мои продавцы» {len(new)}; "
              f"всего продавцов {total} (не больше 30)")
        return {"added": 0, "follow_import": len(new), "follow_total": total}

    def seller_card(uid: str, nick: str, intro: str) -> dict:
        """Для панели на странице профиля: что этот продавец уже дал в ленту."""
        smap = state.get("seller_map", {})
        rows = [r for r in state.get("by_id", {}).values()
                if uid in (str(r.get("seller_id", "")), str(r.get("seller_uid", "")), smap.get(str(r.get("seller_id", "")), ""))]
        ok = [r for r in rows if isinstance(r.get("profit_rub"), int) and r["profit_rub"] > 0
              and r.get("fake_risk") not in ("высокий", "средний")]
        got = db.execute("SELECT data FROM sellers WHERE uid=?", (uid,)).fetchone()
        info = {**(json.loads(got[0]) if got else {}), "uid": uid, "nick": nick}
        return {"kind": "seller", "id": uid, "seller": info, "intro": intro[:120], "feed_n": len(rows), "feed_ok": len(ok),
                "best": max((r["profit_rub"] for r in ok), default=0), **seller_flags(uid, nick)}

    def handle(body: dict) -> dict:
        api_url, page_url, data = str(body.get("apiUrl", "")), str(body.get("pageUrl", "")), body.get("data")
        if not isinstance(data, dict):
            say("  расширение прислало не JSON-объект:", api_url[:90])
            return {"added": 0}
        if "api.mercari.jp" in api_url:
            return mercari_handle(api_url, page_url, data)
        if data.get("probe"):   # только имя запроса: для поиска новых списков вещей
            api = api_url.split("/h5/")[-1].split("/")[0]
            page = urlparse(page_url).path
            seen = state.setdefault("apis", set())
            if (api, page) not in seen:
                seen.add((api, page))
                say(f"  запрос сайта: {api}  (страница {page})")
            return {"added": 0}
        ret = " ".join(str(x) for x in data.get("ret", []))
        if any(sig in ret for sig in THROTTLE_SIGNALS):
            state["blocked_ts"] = time.time()     # автопоиск в app.pyw увидит и выключится
            say(f"\n!!! Сайт сообщил об ограничении/проверке ({ret[:80]}). Сделайте паузу, "
                  "не листайте подряд много страниц.")
            return {"added": 0, "blocked": True}
        page_q = parse_qs(urlparse(page_url).query)
        if any(m in api_url for m in DETAIL_MARKERS):
            iid = (page_q.get("id") or [""])[0]
            d = find_key(data, ["desc", "description", "itemDesc"])
            props = item_props(data)    # «品牌: YSL/圣罗兰 | 成色: 几乎全新 | 尺码: L» — бренда может не быть в тексте
            dump = outdir / "debug_detail.json"
            if not dump.exists():   # образец карточки: проверить, что свойства читаются
                dump.write_text(json.dumps({"props": props, "data": data}, ensure_ascii=False)[:400000], encoding="utf-8")
            if props and not (d and props in str(d)):
                d = f"{d or ''} 【{props}】"
            row, seller, others = parse_detail(data)
            iid = iid or (row or {}).get("id", "")
            now = datetime.now().isoformat(timespec="seconds")
            known = db.execute("SELECT data FROM items WHERE id=?", (iid,)).fetchone()
            changed = False
            if seller:      # шифрованный id из поиска -> числовой: по нему открывается профиль
                enc = json.loads(known[0]).get("seller_id", "") if known else ""
                enc = enc if enc and not enc.isdigit() else ""
                db.execute("INSERT INTO sellers(uid, enc, nick, data, ts) VALUES(?,?,?,?,?) ON CONFLICT(uid) DO UPDATE SET "
                           "enc=CASE WHEN excluded.enc != '' THEN excluded.enc ELSE sellers.enc END, nick=excluded.nick, "
                           "data=excluded.data, ts=excluded.ts",
                           (seller["uid"], enc, seller["nick"], json.dumps(seller, ensure_ascii=False), now))
                db.commit()
            if row and not known and row["price_cny"]:     # вещь открыта не из ленты (ссылка, рекомендации)
                ingest(db, {iid: dict(row, queries="карточка")}, now)
                changed = True
            elif row and known and row["price_cny"] and abs(row["price_cny"] - (json.loads(known[0]).get("price_cny") or 0)) > 0.009:
                # цена в карточке свежее, чем в последней выдаче: панель должна считать по ней (и «подешевело» тоже)
                ingest(db, {iid: {"price_cny": row["price_cny"], "want": row["want"], "queries": ""}}, now)
                changed = True
            fresh = {x["id"]: dict(x, queries="профиль продавца") for x in others
                     if not db.execute("SELECT 1 FROM items WHERE id=?", (x["id"],)).fetchone()}
            if fresh:       # другие его вещи под карточкой — без лишних запросов к сайту
                ingest(db, fresh, now)
                changed = True
            fields = {"desc": str(d).replace("\n", " ")[:700]} if d else {}
            if seller:
                fields.update(seller_uid=seller["uid"], seller_days=seller["days"], seller_good=seller["good"],
                              seller_bad=seller["bad"], seller_sold=seller["sold"])
            if fields and update_fields(db, iid, **fields):
                changed = True
            if changed:
                say(f"Карточка {iid}" + (f": продавец «{seller['nick']}», на 闲鱼 {seller['days']} дн., отзывы "
                                           f"+{seller['good']}/−{seller['bad']}" if seller else "")
                      + (f", ещё его вещей: {len(fresh)}" if fresh else ""))
                rebuild()
            return {"added": 0, "card": card_info(iid, seller)} if iid else {"added": 0}
        if ".shade" in api_url or ".suggest" in api_url:   # подсказки строки поиска, объявлений там нет
            return {"added": 0}
        if any(m in api_url.lower() for m in FOLLOW_MARKERS):  # вы открыли свой список подписок (关注)
            return follow_import(api_url, page_url, page_q, data)
        is_search = any(m in api_url for m in API_MARKERS)
        if not is_search and not any(m in api_url.lower() for m in FEED_MARKERS):
            say("  расширение прислало не поиск:", api_url[:90])
            return {"added": 0}
        # на странице профиля бывают и рекомендации: продавцу приписываем только список его вещей
        uid = (page_q.get("userId") or page_q.get("userid") or [""])[0]
        if "user.page.head" in api_url:   # шапка профиля: настоящий ник продавца и его «о себе»
            base = dig(data, "data", "module", "base", default={})
            nick = str(base.get("displayName", "") if isinstance(base, dict) else "")
            uid = str(dig(data, "data", "baseInfo", "kcUserId", default="") or uid)
            if nick and uid:
                n = 0
                for iid, raw in db.execute("SELECT id, data FROM items WHERE data LIKE ?", (f'%"seller_id": "{uid}"%',)).fetchall():
                    r = json.loads(raw)
                    if r.get("seller") != nick:
                        r["seller"] = nick
                        db.execute("UPDATE items SET data=? WHERE id=?", (json.dumps(r, ensure_ascii=False), iid))
                        n += 1
                db.commit()
                state.setdefault("nicks", {})[uid] = nick
                say(f"Профиль продавца «{nick}» ({uid}): «о себе»: {str(base.get('introduction', ''))[:60]!r}")
                if n:
                    rebuild()
                # панель Щебня на странице профиля: «Следить за продавцом» без открытия его вещей
                return {"added": 0, "card": seller_card(uid, nick, str(base.get("introduction", "")))}
            return {"added": 0}
        profile = ("/personal" in page_url and not is_search
                   and any(m in api_url.lower() for m in ("xyh", "item.list", "personal", "user.page")))
        q = (page_q.get("q") or ["?"])[0] if is_search else "профиль продавца" if profile else "рекомендации"
        items, sold = {}, 0
        entries = dig(data, "data", "resultList", default=[])
        for row in [safe_parse(parse_item, e, "Goofish, выдача") for e in entries] if entries else extract_items(data.get("data")):
            if row and row.pop("sold", False) and profile:    # «综合» в профиле показывает и проданное
                sold += 1
                continue
            if row:
                row["queries"] = q
                if profile and uid:     # все вещи на странице — этого продавца
                    row["seller_id"] = uid
                    row["seller"] = row.get("seller") or state.get("nicks", {}).get(uid) or f"продавец {uid}"
                items[row["id"]] = row
        if profile:
            dump = outdir / "debug_profile.json"
            if not dump.exists():   # образец, чтобы проверить разбор профиля
                dump.write_text(json.dumps({"apiUrl": api_url, "pageUrl": page_url, "data": data},
                                           ensure_ascii=False)[:400000], encoding="utf-8")
            if "item.list" in api_url:  # последние 4 списка вещей профиля: сверить, как сайт помечает проданное
                k = state["plist"] = (state.get("plist", -1) + 1) % 4
                (outdir / f"debug_profile_list_{k}.json").write_text(json.dumps(
                    {"apiUrl": api_url, "pageUrl": page_url, "sold_found": sold, "data": data}, ensure_ascii=False)[:400000],
                    encoding="utf-8")
            if sold:
                say(f"  профиль: пропущено проданных вещей: {sold}")
            if not items:
                if uid:
                    state.setdefault("seller_seen", {})[uid] = {"ts": datetime.now().isoformat(timespec="seconds"),
                                                                "n": 0, "added": 0}
                return {"added": 0, "q": q, "n": 0, "old": 0, "profile": True}
        if not items and not is_search:
            api = re.sub(r"\W+", "_", api_url.split("/h5/")[-1].split("/")[0])[:60]
            dump = outdir / f"debug_feed_{api}.json"
            if not dump.exists():   # образец, чтобы научиться читать новую ленту
                dump.write_text(json.dumps({"apiUrl": api_url, "data": data}, ensure_ascii=False)[:400000], encoding="utf-8")
            return {"added": 0}
        if not is_search and any(not r["image"] for r in items.values()):
            dump = outdir / "debug_feed_noimage.json"   # образец карточки без фото, чтобы найти поле картинки
            if not dump.exists():
                dump.write_text(json.dumps({"apiUrl": api_url, "data": data}, ensure_ascii=False)[:400000], encoding="utf-8")
        if not items:
            dump = outdir / "debug_unknown_search.json"
            if not dump.exists():   # один образец незнакомого ответа, чтобы научиться его читать
                dump.write_text(json.dumps({"apiUrl": api_url, "data": data}, ensure_ascii=False)[:400000], encoding="utf-8")
            say(f"  «{q}»: ответ поиска без объявлений (ret: {ret[:80]}; ключи data: "
                  f"{', '.join(list((data.get('data') or {}).keys())[:8])})")
            qe = state.setdefault("qempty", {})     # «Главная»: поиск, который раз за разом пуст, — в «можно убрать»
            qe[q] = qe.get(q, 0) + 1
            return {"added": 0, "q": q, "n": 0, "old": 0, "search": True}     # вкладка «Автопоиск» покажет «пусто»
        seen_before = db.execute(
            f"SELECT id, price, first_seen FROM items WHERE id IN ({','.join('?' * len(items))})", list(items)).fetchall()
        old_prices = {i: p for i, p, _ in seen_before}
        known = set(old_prices)
        added = len(set(items) - known)
        run_add(len(items), set(items) - known)
        # «старые» — известные дольше 2 минут: свежие вещи из «综合» той же выдачи не считаются, иначе автопоиск
        # решит, что догнал всё новое, хотя дальше по «新发布» ещё есть невиданное
        cut = (datetime.now() - timedelta(minutes=2)).isoformat(timespec="seconds")
        old = sum(1 for _, _, fs in seen_before if fs and fs < cut)
        n_pre = premod_new(items, known)
        ingest(db, items, datetime.now().isoformat(timespec="seconds"))
        api = "" if is_search else " [" + api_url.split("/h5/")[-1].split("/")[0] + ", " + urlparse(page_url).path + "]"
        fresh = sorted(r["posted"] for r in items.values() if r.get("posted"))
        age = (f", самое свежее выложено {fmt_age(fresh[-1])} назад" if fresh and is_search else "")
        say(f"«{q}»: {len(items)} объявлений, из них новых: {added}{age}{api}" + (f", мусор на входе: {n_pre}" if n_pre else ""))
        state.setdefault("qempty", {}).pop(q, None)
        qs = state.setdefault("queries", {}).setdefault(q, {"pages": 0, "items": 0, "new": 0})
        qs.update(pages=qs["pages"] + 1, items=qs["items"] + len(items), new=qs["new"] + added,
                  at=datetime.now().strftime("%H:%M"))
        if profile and uid:     # «Мои продавцы» на «Главной»: когда профиль открывали и сколько нового нашли
            prev = state.setdefault("seller_seen", {}).get(uid) or {}
            fresh_visit = not prev or prev.get("ts", "") < cut     # вторая страница того же захода — суммируем
            state["seller_seen"][uid] = {"ts": datetime.now().isoformat(timespec="seconds"),
                                         "n": len(items) + (0 if fresh_visit else prev.get("n", 0)),
                                         "added": added + (0 if fresh_visit else prev.get("added", 0))}
        if added > n_pre:       # «Главная»: зелёная точка у поиска, который принёс новое за последний час
            state.setdefault("qnew_at", {})[q] = time.time()
        # ничего нового и цены те же (так почти всегда) — ленту не пересчитываем сейчас: это 2–3 с, и окно в это время
        # подтормаживает. Счётчики запросов и «последний раз видели» досчитает фоновый пересчёт (lazy_bg)
        moved = any(r["price_cny"] and old_prices.get(i) and abs(r["price_cny"] - old_prices[i]) > 0.009
                    for i, r in items.items())
        if added > n_pre or moved:
            # новое или подешевело: пересчёт не сразу, а один на несколько ответов подряд (lazy_bg, ≤15 с) — уведомления
            # и итоги проб «中/EN» считаются после него, по свежей ленте
            state.setdefault("after_build", []).append((items, old_prices, q, is_search, added))
            state["dirty_new"] = True
        else:
            state["dirty"] = True
            if is_search:
                trial_note(q, len(items), sum(1 for i in items if i in state.get("by_id", {})), added)
        return {"added": added, "q": q, "n": len(items), "old": old, "search": is_search, "profile": profile}

    def add_alert(kind: str, r: dict, q: str = "", old=None, text: str = ""):
        # одна и та же вещь — один раз (снижение цены — снова, если цена новая)
        key = (kind, r.get("id", ""), None if kind == "new" else r.get("price_cny"))
        sent = state.setdefault("alerted", set())
        if r and key in sent:
            return
        sent.add(key)
        state["alert_seq"] = state.get("alert_seq", 0) + 1
        al = state.setdefault("alerts", [])
        # цена для окна и Telegram: у Mercari — в иенах (в ленте price_cny у неё юань-эквивалент)
        f_ = lambda v: f"{v:,.0f}".replace(",", " ")
        if r and r.get("site") == "mercari":
            yen = lambda v: v * COSTS["rate"] / max(COSTS["jpy_rate"], 1e-6)
            ptxt = f"¥{f_(r.get('price_jpy') or yen(r.get('price_cny') or 0))} JP" + (f" (было ¥{f_(yen(old))})" if old else "")
        else:
            ptxt = f"¥{f_(r.get('price_cny') or 0)}" + (f" (было ¥{f_(old)})" if old else "") if r else ""
        al.append({"seq": state["alert_seq"], "kind": kind, "id": r.get("id", ""), "q": q, "url": r.get("url", ""),
                   "image": r.get("image", ""), "title": f'{r.get("brand", "")} · {r.get("model") or r.get("category", "")}' if r else "",
                   "price_cny": r.get("price_cny"), "old_price": old, "profit_rub": r.get("profit_rub"), "text": text,
                   "age": fmt_age(r.get("posted", "")) if r else "", "site": r.get("site", "") if r else "", "price_txt": ptxt})
        del al[:-50]

    def push_alerts(items: dict, old_prices: dict, q: str):
        """Уведомления (Windows + Telegram, app.pyw забирает через /alerts):
        новая вещь уровня A; перевыставлена дешевле; снизилась цена у ★ или уровня A."""
        mxs = {(s.get("site", ""), s["q"]): s["max"] for s in load_searches()["items"]}
        amin = COSTS.get("alert_min", 3000)
        for iid in items:
            r = state.get("by_id", {}).get(iid)
            mx = mxs.get((r.get("site", ""), q)) if r else None
            # «до ¥» у поиска Mercari — в иенах
            if not r or r.get("mark") == "hide" or (mx and (r.get("price_jpy") if r.get("site") == "mercari"
                                                           else r["price_cny"]) > mx):
                continue
            # «уровень A» — лишь «цена не подозрительно низкая» (так у ~80% ленты): без прибыли от порога не зовём
            good = isinstance(r.get("profit_rub"), int) and r["profit_rub"] >= amin
            hot = r.get("mark") == "like" or (r.get("tier") == "A" and good)
            old = old_prices.get(iid)
            if old is None:
                rp = to_float(r.get("relist_price"))
                if rp and r["price_cny"] < rp - 0.01 and hot:
                    add_alert("relist", r, q, rp)
                elif r.get("tier") == "A" and good:
                    add_alert("new", r, q)
            elif hot and r["price_cny"] < old - 0.01:
                add_alert("drop", r, q, old)
        # новые вещи под заказы клиентов (размер должен совпасть, не «размер?»)
        for o in load_orders()["items"]:
            for iid in items:
                r = state.get("by_id", {}).get(iid)
                if o["active"] and r and iid not in old_prices and order_fit(o, r) == "да":
                    pr = profit_calc(r["price_cny"], o["budget"], a, cat, r.get("ckey") or "?", r.get("kg") or None, r.get("site", ""))["profit_rub"]
                    if pr > 0:
                        add_alert("order", {**r, "profit_rub": pr}, q, text=o["client"] or "клиент")

    class Handler(BaseHTTPRequestHandler):
        def reply(self, code: int, body, ctype="application/json"):
            raw = body.encode("utf-8") if isinstance(body, str) else json.dumps(body).encode("utf-8")
            self.send_response(code)
            # телефону через Tailscale лента (~2 МБ) идёт сжатой — в разы быстрее через ретранслятор
            if len(raw) > 30000 and not self.local() and "gzip" in self.headers.get("Accept-Encoding", ""):
                import gzip
                raw = gzip.compress(raw, 5)
                self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                pass    # окно не дождалось ответа и закрыло соединение — не ошибка сервера

        def read_json(self):
            return json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))

        def local(self) -> bool:
            return self.client_address[0] in ("127.0.0.1", "::1")

        def gate(self) -> bool:
            """Компьютер — без ключа; телефон (адрес Tailscale) — только с ключом и никогда от имени приложения."""
            if self.local():
                return True
            import hmac
            on, tok = phone_cfg()
            if not (on and tok) or self.headers.get("X-Goofish-Ext"):
                self.reply(403, {"error": "forbidden"})
                return False
            cookies = dict(c.strip().split("=", 1) for c in self.headers.get("Cookie", "").split(";") if "=" in c)
            if hmac.compare_digest(cookies.get("gtok", ""), tok):
                return True
            if self.command == "GET" and hmac.compare_digest((parse_qs(urlparse(self.path).query).get("t") or [""])[0], tok):
                self.send_response(302)     # ключ из ссылки — в cookie телефона, из адреса убираем
                # Lax, а не Strict: ссылку открывают из камеры/мессенджера — для Strict такой переход «чужой», и cookie не дойдёт
                self.send_header("Set-Cookie", f"gtok={tok}; Max-Age=31536000; Path=/; HttpOnly; SameSite=Lax")
                self.send_header("Location", urlparse(self.path).path or "/")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return False
            self.reply(403, '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
                            '<p style="font:16px sans-serif;padding:20px">Нет доступа. Откройте ссылку с ключом '
                            '(вкладка «Статус» на компьютере → «Доступ с телефона»).</p>', "text/html")
            return False

        def do_GET(self):
            if not self.gate():
                return
            if self.headers.get("Host") not in hosts:
                return self.reply(403, {"error": "forbidden"})
            if self.path == "/boot":        # заставка спрашивает, готова ли лента; без замка — отвечает и во время пересчёта
                return self.reply(200, {"ready": bool(state.get("ready")), "sec": round(time.time() - state["started"], 1),
                                        "eta": state.get("boot_eta", 0), "rows": state.get("boot_rows", 0),
                                        "good": state.get("good", 0) if state.get("ready") else 0,
                                        "top": state.get("top", 0) if state.get("ready") else 0})
            if not state.get("ready"):      # лента ещё считается
                if self.path == "/version":
                    return self.reply(200, {"version": 0, "loading": True})
                if "text/html" in self.headers.get("Accept", ""):
                    return self.reply(200, splash_page(state.get("boot_eta", 0), state.get("boot_prev")), "text/html")
            if self.path == "/phone":
                on, tok = phone_cfg()
                ip = phone["ip"] or (tailscale_ip() if on else "")
                return self.reply(200, {"on": on, "running": phone["srv"] is not None, "ip": ip, "err": phone["err"],
                                        "url": f"http://{ip}:8765/?t={tok}" if on and tok and ip and self.local() else ""})
            if self.path.startswith("/run/summary"):     # сводка прогона: просмотрено, новых, ошибок, отсеяно по подлинности
                with lock:
                    return self.reply(200, {"text": run_summary(state.get("rejects", {}), reset="reset=1" in self.path)})
            if self.path == "/orders":
                with lock:
                    return self.reply(200, {**load_orders(), "matches": state.get("order_matches", {}), "leads": state.get("leads", []),
                                            "brands": {**{b["name"]: [m["name"] for m in b.get("models", [])] for b in cat["brands"].values()},
                                                       **{b["name"]: [] for b in (cat.get("outside") or {}).get("interesting", [])}}})
            if self.path == "/":        # оболочка ленты (~100 КБ); без замка — открывается и во время пересчёта
                return self.reply(200, state["html"], "text/html")
            if self.path.startswith("/feed?") or self.path == "/feed":      # вещи ленты по 50 штук, фильтры и сортировка здесь
                p = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
                return self.reply(200, feed_query(state.get("feed") or [], p, state.get("order_ids")))
            if self.path == "/version":
                # утренняя сводка: раз в день после 8:00; не в первые секунды — окно приложения ещё не слушает уведомления
                if (datetime.now().hour >= 8 and time.time() - state["started"] > 30 and not state.get("summary_today")
                        and str(ai_settings().get("summary_last", ""))[:10] < date.today().isoformat()):
                    state["summary_today"] = True
                    with lock:
                        try:
                            morning_summary(str(ai_settings().get("summary_last", "")))
                        except Exception as e:
                            say("Утренняя сводка не собралась:", repr(e)[:160])
                elif state.get("summary_today") and str(ai_settings().get("summary_last", ""))[:10] < date.today().isoformat():
                    state["summary_today"] = False      # наступил новый день, приложение не закрывали
                if time.time() - state.get("tg_scan", 0) > 60:     # новые экспорты Telegram Desktop — раз в минуту
                    state["tg_scan"] = time.time()
                    with lock:
                        try:
                            import_tg_exports(db)
                        except Exception as e:
                            say("Рынок: импорт из Telegram Desktop:", repr(e)[:160])
                mc = load_market_cfg()
                due = bool(state.get("market_run")) or (bool(mc["channels"]) and not state.get("market_running") and (
                    not mc["last_run"] or mc["last_run"] < (datetime.now() - timedelta(days=mc["every_days"])).isoformat()))
                kick = state.pop("watch_kick", False)      # «Обновить» у «Моих поисков»: новый круг автопоиска сразу
                return self.reply(200, {"v": state["version"], "ext": state.get("ext", 0), "market_due": due, "watch_kick": kick,
                                        "new": state.get("new", 0), "new_good": state.get("new_good", 0)})
            if self.path == "/market/config":
                with lock:
                    st = market_stats(db, cat)
                return self.reply(200, {**market_cfg_public(load_market_cfg()), "stats": st,
                                        "running": bool(state.get("market_running")), "last": state.get("market_last", "")})
            if self.path == "/tg/config":
                s = ai_settings()
                return self.reply(200, {"token": s.get("tg_bot_token", ""), "chat_id": s.get("tg_chat_id", ""),
                                        "min_profit": to_float(s.get("tg_min_profit", 0))})
            if self.path.startswith("/alerts"):
                after = int((parse_qs(urlparse(self.path).query).get("after") or ["0"])[0] or 0)
                al = [x for x in list(state.get("alerts", [])) if x["seq"] > after]     # без lock: окно ждёт не дольше 2 с
                return self.reply(200, {"seq": state.get("alert_seq", 0), "items": al, "blocked": state.get("blocked_ts", 0)})
            if self.path == "/watch":       # живая строка автопоиска на «Главной» (состояние присылает app.pyw)
                w, qs = state.get("watch", {}), state.get("queries", {})
                # «Круг автопоиска»: сколько новых вещей каждый поиск круга принёс за сеанс
                yields = {f"{s}|{q}": (qs.get(q) or {}).get("new", 0) for q, s in (w.get("plan") or [])}
                return self.reply(200, {**w, "yields": yields, "now": time.time()})
            if self.path == "/stats":       # «Аналитика» на «Главной»: готовое из фонового кэша (stats_bg), без замка
                return self.reply(200, state.get("stats") or {"funnel": [], "heat": [], "tz": 0, "hour": 0, "pending": True})
            if self.path == "/searches":
                s = load_searches()     # «мало толку» и «rare» — автопоиск заходит в такие запросы раз в 3 круга
                slow = slow_searches(state, s["items"])
                for it in s["items"]:
                    k_ = skey(it["q"], it.get("site", ""))
                    it["junk"] = (bool(it.get("rare")) or query_junk(state.get("qstats", {}), k_)
                                  or (state.get("qempty", {}).get(k_, 0) >= 3 and k_ not in state.get("qstats", {})))
                    it["slow"] = k_ in slow     # 3 дня без вещей 70+ — раз в 4 круга
                    if it.get("pin"):           # вы закрепили «всегда каждый круг» — сильнее автоматики
                        it["junk"] = it["slow"] = False
                qh = state.get("quiet_hours")      # автопоиск ждёт этот ответ — не стоим в очереди за пересчётом ленты
                if qh and time.time() - qh[0] < 1800:
                    s["quiet"], s["quiet_note"] = qh[1], qh[2]
                else:
                    with lock:
                        s["quiet"], s["quiet_note"] = quiet_hours(db, state)
                return self.reply(200, s)
            if self.path == "/suggestions":
                with lock:
                    out = {"sug": search_suggestions(state, cat), "qst": state.get("qstats", {}), "live": home_live(state),
                           "qempty": state.get("qempty", {}),
                           "searches": load_searches()}
                return self.reply(200, out)
            if self.path == "/learn":
                with lock:
                    html = learn_html()
                return self.reply(200, html, "text/html")
            if self.path == "/rate":
                with lock:
                    items = rate_queue(list(state.get("by_id", {}).values()))
                return self.reply(200, rate_page(items), "text/html")
            if self.path == "/goofish_market.json":
                with lock:
                    since = (db.execute("SELECT MIN(first_seen) FROM items").fetchone()[0] or "")[:10]
                    out = {"items": goofish_market(list(state.get("by_id", {}).values())), "since": since, "dem_min": DEM_MIN_N}
                return self.reply(200, out)
            if self.path == "/price_suggestions.json":
                f = Path(a.catalog).with_name("price_suggestions.json")
                sug = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {"items": []}
                with lock:
                    sug["items"] = sug.get("items", []) + market_suggestions(db, cat)
                return self.reply(200, sug)
            if self.path == "/catalog.json":
                with lock:
                    return self.reply(200, cat)
            if self.path.startswith("/img?"):
                # фото вещи через свой адрес: только так страница может положить картинку в буфер обмена
                u = (parse_qs(urlparse(self.path).query).get("u") or [""])[0]
                host = urlparse(u).hostname or ""
                # фото Goofish (alicdn) и Mercari (mercdn / mercari-shops-static)
                if not any(host == d or host.endswith("." + d) for d in ("alicdn.com", "mercdn.net", "mercari-shops-static.com")):
                    return self.reply(400, {"error": "not a goofish/mercari image"})
                try:
                    import urllib.request
                    req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req, timeout=20) as r:
                        raw, ctype = r.read(), r.headers.get("Content-Type", "image/jpeg")
                except Exception as e:
                    return self.reply(502, {"error": repr(e)[:120]})
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            if self.path == "/home":
                with lock:
                    html = home_page(state, cat)
                return self.reply(200, html, "text/html")
            if self.path == "/basket":
                with lock:
                    return self.reply(200, basket_view())
            if self.path == "/deals":
                with lock:
                    html = deals_page(load_deals(db), cat, a.tax, state.get("rate_note", ""))
                return self.reply(200, html, "text/html")
            if self.path in ("/status", "/catalog", "/prices"):
                with lock:
                    html = side_page(self.path[1:], state, cat, prices, outdir)
                return self.reply(200, html, "text/html")
            self.reply(404, {"error": "not found"})

        def do_POST(self):
            # свои заголовки ставят только расширение и сам отчёт: обычный сайт не сможет
            # ни подсунуть данные, ни поставить отметки
            if not self.gate():
                return
            ui = self.headers.get("X-Goofish-UI") == "1" and self.headers.get("Host") in hosts
            if self.path == "/phone" and ui and self.local():      # включить/выключить можно только с компьютера
                try:
                    on = bool(self.read_json().get("on"))
                except Exception:
                    return self.reply(400, {"error": "bad json"})
                import secrets
                # выключение отзывает ключ: старая ссылка на телефоне перестаёт работать
                save_settings_keys(phone_on=on, phone_token=secrets.token_urlsafe(18) if on else "")
                phone["err"] = ""
                return self.reply(200, {"ok": True})
            if self.path == "/lead" and ui:        # разобрать вставленный текст «ищу…» в заготовку заказа (ничего не сохраняет)
                try:
                    body = self.read_json()
                except Exception:
                    return self.reply(400, {"error": "bad json"})
                return self.reply(200, {"lead": parse_lead(str(body.get("text") or "")[:3000], cat)})
            if self.path == "/orders" and ui:
                try:
                    body = self.read_json()
                except Exception:
                    return self.reply(400, {"error": "bad json"})
                with lock:
                    o = save_orders(body)
                    rebuild()
                    return self.reply(200, {**o, "matches": state.get("order_matches", {})})
            if self.path == "/items" and self.headers.get("X-Goofish-Ext") == "1":
                state["ext"] = state.get("ext", 0) + 1
                if state["ext"] == 1:
                    say("Расширение на связи")
                try:
                    body = self.read_json()
                except Exception:
                    return self.reply(400, {"error": "bad json"})
                try:
                    with lock:
                        res = handle(body)
                except Exception as e:
                    say("  ошибка обработки:", repr(e)[:200])
                    res = {"added": 0}
                return self.reply(200, res)
            if self.path.startswith(("/market/", "/tg/")) and self.path not in ("/market/config", "/tg/test") \
                    and self.headers.get("X-Goofish-Ext") == "1":
                # от app.pyw: посты каналов/карточки площадок, ход сбора рынка, ответы Telegram
                try:
                    body = self.read_json()
                except Exception:
                    return self.reply(400, {"error": "bad json"})
                with lock:
                    if self.path == "/market/items":
                        src = str(body.get("market", ""))[:80]
                        n = market_ingest(db, src, body.get("items") or [])
                        if body.get("error"):
                            say(f"Рынок: {src}: {str(body['error'])[:120]}")
                        elif n or src.startswith("tg:"):
                            say(f"Рынок: {src}: постов {len(body.get('items') or [])}, новых {n}")
                        return self.reply(200, {"added": n})
                    if self.path == "/market/started":
                        state.update(market_run=False, market_running=True)
                        c = load_market_cfg()
                        c["last_run"] = datetime.now().isoformat(timespec="seconds")
                        save_market_cfg(c)
                        say(f"Рынок: сбор цен из {len(c['channels'])} каналов")
                        return self.reply(200, {"ok": True})
                    if self.path == "/market/done":
                        st = market_stats(db, cat)
                        state.update(market_running=False, market_last=datetime.now().strftime("%d.%m %H:%M"))
                        say(f"Рынок: готово, в базе {st['posts']} постов, с ценой и брендом {st['parsed']}")
                        return self.reply(200, {"ok": True})
                    if self.path == "/tg/chat":
                        save_settings_keys(tg_chat_id=str(body.get("chat_id", ""))[:40])
                        return self.reply(200, {"ok": True})
                    if self.path == "/tg/status":
                        state["tg_status"] = (("работает" if body.get("ok") else "ошибка: " + str(body.get("error", ""))[:200])
                                              + " · " + datetime.now().strftime("%H:%M"))
                        if not body.get("ok"):
                            say("Telegram:", state["tg_status"])
                        return self.reply(200, {"ok": True})
                return self.reply(404, {"error": "not found"})
            if (self.path in ("/market/config", "/tg/test") and self.headers.get("X-Goofish-UI") == "1"
                    and self.headers.get("Host") in hosts):
                try:
                    body = self.read_json()
                except Exception:
                    return self.reply(400, {"error": "bad json"})
                with lock:
                    if self.path == "/tg/test":
                        add_alert("test", {}, text="Проверка связи: уведомления Sheben'Store работают.")
                        return self.reply(200, {"ok": True})
                    if body.get("import"):
                        state["tg_scan"] = time.time()
                        try:
                            note = import_tg_exports(db)
                        except Exception as e:
                            note = "ошибка: " + repr(e)[:160]
                        return self.reply(200, {**market_cfg_public(load_market_cfg()), "stats": market_stats(db, cat),
                                                "found": note})
                    c = load_market_cfg()
                    c.update({k: body[k] for k in ("channels", "factor", "every_days", "pages") if k in body})
                    c = save_market_cfg(c)
                    if body.get("run"):
                        state["market_run"] = True
                return self.reply(200, market_cfg_public(c))
            if (self.path in ("/learn/apply", "/learn/dismiss", "/learn/restore") and self.headers.get("X-Goofish-UI") == "1"
                    and self.headers.get("Host") in hosts):
                try:
                    key = str(self.read_json()["key"])
                except Exception:
                    return self.reply(400, {"errors": ["не удалось прочитать данные"]})
                with lock:
                    if self.path.endswith(("dismiss", "restore")):
                        if self.path.endswith("dismiss"):
                            db.execute("INSERT OR REPLACE INTO learn_dismissed VALUES(?,?)",
                                       (key, datetime.now().isoformat(timespec="seconds")))
                        else:
                            db.execute("DELETE FROM learn_dismissed WHERE key=?", (key,))
                        db.commit()
                        if key.startswith("rule:"):     # правило из скрытий: лента меняется сразу
                            rebuild()
                        return self.reply(200, {"ok": True})
                    res = learn(db, load_rows(db, session_start), a, cat, prices)
                    s = next((s for s in res["suggestions"] if s["key"] == key), None)
                    if s is None:
                        return self.reply(400, {"errors": ["подсказка уже неактуальна, обновите страницу"]})
                    new = json.loads(json.dumps(cat, ensure_ascii=False))
                    apply_suggestion(new, s["op"])
                    errors = validate_catalog(new)
                    if errors:
                        return self.reply(400, {"errors": errors[:10]})
                    path = Path(a.catalog)
                    backup = save_catalog(path, new)
                    cat.clear()
                    cat.update(load_catalog(path))
                    _EVAL_CACHE.clear()
                    unarchive_recent(db)
                    rebuild()
                say(f"Обучение: применено «{s['text']}» (копия каталога: {backup.name})")
                return self.reply(200, {"ok": True})
            if (self.path == "/catalog" and self.headers.get("X-Goofish-UI") == "1"
                    and self.headers.get("Host") in hosts):
                try:
                    new = self.read_json()
                except Exception:
                    return self.reply(400, {"errors": ["не удалось прочитать данные"]})
                errors = validate_catalog(new)
                if errors:
                    return self.reply(400, {"errors": errors[:30]})
                with lock:
                    path = Path(a.catalog)
                    backup = save_catalog(path, new)
                    cat.clear()
                    cat.update(load_catalog(path))
                    _EVAL_CACHE.clear()
                    unarchive_recent(db)
                    rebuild()
                say(f"Каталог сохранён из приложения (копия: {backup.name})")
                return self.reply(200, {"ok": True, "backup": backup.name, "good": state.get("good", 0)})
            if (self.path == "/mark" and self.headers.get("X-Goofish-UI") == "1"
                    and self.headers.get("Host") in hosts):
                try:
                    body = self.read_json()
                    iid, mark = str(body["id"]), str(body.get("mark", ""))
                    reason = str(body.get("reason", ""))
                    reason = reason if reason in HIDE_REASONS and mark == "hide" else ""
                except Exception:
                    return self.reply(400, {"error": "bad json"})
                if mark not in ("", "like", "hide"):
                    return self.reply(400, {"error": "bad mark"})
                with lock:
                    junk = state.get("by_id", {}).get(iid, {}).get("outside_brand", "") if reason == JUNK_REASON else ""
                    set_mark(db, iid, mark, reason)
                    if junk:   # бренд из «интересных» переезжает в «шлак»
                        new = json.loads(json.dumps(cat, ensure_ascii=False))
                        o = new["outside"]
                        b = next((b for b in o["interesting"] if b["name"] == junk), None)
                        if b:
                            o["interesting"].remove(b)
                            o["junk"] += [w for w in b["words"] if w not in o["junk"]]
                            path = Path(a.catalog)
                            backup = save_catalog(path, new)
                            cat.clear()
                            cat.update(load_catalog(path))
                            _EVAL_CACHE.clear()
                            unarchive_recent(db)
                            say(f"Бренд {junk} перенесён в шлак (копия каталога: {backup.name})")
                    rebuild()
                return self.reply(200, {"ok": True})
            if (self.path in ("/deal", "/deal/update", "/costs", "/deal/manual", "/market/manual", "/basket")
                    and self.headers.get("X-Goofish-UI") == "1"
                    and self.headers.get("Host") in hosts):
                try:
                    body = self.read_json()
                except Exception:
                    return self.reply(400, {"error": "bad json"})
                with lock:
                    try:
                        res = deals_post(self.path, body)
                    except ValueError as e:
                        return self.reply(400, {"error": str(e)})
                return self.reply(200, res)
            if (self.path == "/searches" and self.headers.get("X-Goofish-UI") == "1"
                    and self.headers.get("Host") in hosts):
                try:
                    body = self.read_json()
                    assert isinstance(body, dict) and isinstance(body.get("items"), list)
                except Exception:
                    return self.reply(400, {"error": "bad json"})
                return self.reply(200, save_searches(body))
            if self.path == "/mark/unhide" and self.headers.get("X-Goofish-Ext") == "1":   # «Вернуть в ленту» на панели
                try:
                    iid = str(self.read_json()["id"])
                except Exception:
                    return self.reply(400, {"error": "bad json"})
                with lock:
                    set_mark(db, iid, "", "")
                    rebuild()
                    row = db.execute("SELECT data FROM items WHERE id=?", (iid,)).fetchone()
                    uid = str(json.loads(row[0]).get("seller_uid") or "") if row else ""
                    sel = db.execute("SELECT data FROM sellers WHERE uid=?", (uid,)).fetchone() if uid else None
                    card = card_info(iid, json.loads(sel[0]) if sel else None)
                say(f"Вещь {iid} возвращена в ленту с панели")
                return self.reply(200, {"card": card})
            if self.path == "/watch/state" and self.headers.get("X-Goofish-Ext") == "1":
                try:
                    body = self.read_json()
                    assert isinstance(body, dict)
                except Exception:
                    return self.reply(400, {"error": "bad json"})
                state["watch"] = body
                return self.reply(200, {"ok": True})
            if self.path == "/searches/refresh" and ui:    # «Обновить» у «Моих поисков» на «Главной»
                state["chan_day"] = ""      # поиски из каналов — пересобрать сейчас (в фоне: может спросить Gemini)
                threading.Thread(target=searches_refresh, daemon=True).start()     # и «из избранного»
                state["watch_kick"] = True
                return self.reply(200, {"ok": True})
            # «Мои продавцы»: с «Главной» или с панели на странице товара (через app.pyw)
            if self.path == "/sellers/follow" and (ui or self.headers.get("X-Goofish-Ext") == "1"):
                try:
                    body = self.read_json()
                    uid, on, every = str(body.get("uid", "")), bool(body.get("on", True)), body.get("every")
                    assert uid.isdigit()
                except Exception:
                    return self.reply(400, {"error": "bad json"})
                with lock:
                    s = load_searches()
                    old = next((x for x in s["items"] if x.get("seller") == uid), None)
                    # «каждый круг» (every) — для лучших; обычный продавец — раз в 3 круга
                    rare = (not every) if every is not None else (old.get("rare", False) if old else True)
                    if on:
                        got = db.execute("SELECT nick FROM sellers WHERE uid=?", (uid,)).fetchone()
                        nick = (str(body.get("nick") or "") or (got[0] if got else "") or state.get("nicks", {}).get(uid) or uid)[:40]
                        site = str(body.get("site") or (old or {}).get("site") or "")     # продавец Mercari — свой профиль
                        new = {"q": old["q"] if old else f"продавец {nick}", "seller": uid, "rare": rare} \
                            | ({"site": "mercari"} if site == "mercari" else {})
                        s["items"] = [new if x is old else x for x in s["items"]] if old else s["items"] + [new]
                    else:
                        s["items"] = [x for x in s["items"] if x is not old]
                    s = save_searches(s)
                    ent = next((x for x in s["items"] if x.get("seller") == uid), None)
                say(f"Мои продавцы: {'убран' if not ent else 'каждый круг' if not ent.get('rare') else 'раз в 3 круга'} — {uid}")
                return self.reply(200, {"ok": True, "followed": bool(ent), "every": bool(ent and not ent.get("rare")),
                                        "searches": s})
            if (self.path == "/open/gemini" and self.headers.get("X-Goofish-UI") == "1"
                    and self.headers.get("Host") in hosts):
                import webbrowser       # Gemini — в вашем обычном браузере, где вы уже вошли в Google
                webbrowser.open("https://gemini.google.com/app")
                return self.reply(200, {"ok": True})
            if (self.path == "/ai/ask" and self.headers.get("X-Goofish-UI") == "1"
                    and self.headers.get("Host") in hosts):
                # мини-окно Gemini: проверка фото сразу (без очереди) или уточняющий вопрос
                try:
                    body = self.read_json()
                    iid, question = str(body["id"]), str(body.get("question", "")).strip()
                    history = body.get("history") if isinstance(body.get("history"), list) else []
                except Exception:
                    return self.reply(400, {"error": "bad json"})
                ck = state.get("checker")
                if ck is None or not ai_settings().get("gemini_api_key"):
                    return self.reply(400, {"error": "нет ключа Gemini: впишите его в settings.json"})
                with lock:
                    row = next((r for r in load_rows(db, session_start) if r["id"] == iid), None)
                if not row or not row.get("image"):
                    return self.reply(400, {"error": "у вещи нет фото"})
                try:
                    if question:
                        return self.reply(200, {"answer": ck.ask(row, question, history)})
                    res = row.get("ai")
                    if not res:
                        res = ck.check(row)
                        ck.save(iid, res)
                        threading.Thread(target=ai_done, args=(iid,), daemon=True).start()   # пересчёт ленты в фоне
                    return self.reply(200, {"result": res})
                except Exception as e:
                    code = getattr(e, "code", 0)
                    msg = ("Gemini перегружен, попробуйте через минуту" if code in (500, 503, 504) else
                           "у бесплатного Gemini на сегодня кончился лимит у всех моделей — завтра снова"
                           if "исчерпали" in str(e) or (code == 429 and "PerDay" in str(e)) else
                           "слишком много запросов подряд — подождите минуту" if code == 429 else f"ошибка: {repr(e)[:160]}")
                    return self.reply(502, {"error": msg})
            if (self.path == "/ai/similar" and self.headers.get("X-Goofish-UI") == "1"
                    and self.headers.get("Host") in hosts):
                # кнопка «Похожее»: Gemini по фото пишет запрос для Goofish; без ключа/лимита — текстовый запрос карточки
                try:
                    iid = str(self.read_json()["id"])
                except Exception:
                    return self.reply(400, {"error": "bad json"})
                with lock:
                    row = state.get("by_id", {}).get(iid)
                if not row:
                    return self.reply(404, {"error": "вещь не найдена в ленте"})
                fallback = row.get("sq") or ""
                ck = state.get("checker")
                if ck is None or not ai_settings().get("gemini_api_key") or not row.get("image"):
                    return self.reply(200, {"q": fallback, "fallback": True})
                try:
                    q = ck.photo_query(row, hint=fallback)
                    say(f"Похожее по фото: «{row.get('title', '')[:30]}» → «{q}»")
                    return self.reply(200, {"q": q or fallback, "fallback": not q})
                except Exception as e:
                    say("Похожее по фото: Gemini не ответил:", repr(e)[:140])
                    return self.reply(200, {"q": fallback, "fallback": True})
            if (self.path == "/ai/check" and self.headers.get("X-Goofish-UI") == "1"
                    and self.headers.get("Host") in hosts):
                try:
                    iid = str(self.read_json()["id"])
                except Exception:
                    return self.reply(400, {"error": "bad json"})
                ck = state.get("checker")
                if ck is None or not ai_settings().get("gemini_api_key"):
                    return self.reply(400, {"error": "нет ключа Gemini: впишите его в settings.json"})
                with lock:
                    row = next((r for r in load_rows(db, session_start) if r["id"] == iid), None)
                if not row or not row.get("image"):
                    return self.reply(400, {"error": "у вещи нет фото"})
                ck.push_front(row)
                return self.reply(200, {"ok": True, "queued": len(ck.queue)})
            self.reply(403, {"error": "forbidden"})

        def log_message(self, *args):
            pass

    ai_flush = {"t": 0.0, "pending": False}

    def ai_rebuild():
        # не пересчитываем сами: помечаем, и lazy_bg сделает один пересчёт на всех (поиск, ИИ, отпечатки фото).
        # Раньше три фоновые задачи пересчитывали ленту каждая по своим часам, подряд, и сервер по 10+ с
        # не принимал ответы поиска (12.11: 146 раз за сеанс)
        ai_flush.update(t=time.time(), pending=False)
        state["dirty_new"] = True

    def ai_done(iid):
        # ответы ИИ приходят часто: пересчитываем ленту не чаще раза в минуту
        if time.time() - ai_flush["t"] >= 60:
            ai_rebuild()
        elif not ai_flush["pending"]:
            ai_flush["pending"] = True
            threading.Timer(60, ai_rebuild).start()

    def learn_key():
        """«Обучение» меняется только от ваших ★/✕, отказов от подсказок и правок каталога."""
        return (db.execute("SELECT COUNT(*), MAX(ts) FROM marks_log").fetchone(),
                db.execute("SELECT COUNT(*) FROM learn_dismissed").fetchone(),
                hash(json.dumps(cat, sort_keys=True, ensure_ascii=False)))

    def learn_html() -> str:
        """Страница «Обучения» из памяти (расчёт ~5 с); пересчёт — только если что-то из learn_key поменялось. Под lock."""
        k = learn_key()
        c = state.get("learn_cache")
        if not c or c[0] != k:
            c = state["learn_cache"] = (k, learn_page(learn(db, load_rows(db, session_start), a, cat, prices)))
        return c[1]

    def lazy_bg():
        """Ответы поиска ленту сразу не пересчитывают (это 1,5–2,5 с, окно в это время подтормаживает). Здесь:
        принесли новое — пересчёт не чаще раза в 15 с, потом уведомления; ничего нового — раз в 3 минуты."""
        n = 0
        while True:
            time.sleep(2)
            n += 1
            if n % 60 == 0:     # раз в 2 минуты: «Обучение» заранее, чтобы вкладка открывалась сразу
                try:
                    with lock:
                        learn_html()
                except Exception as e:
                    say("Обучение не посчиталось:", repr(e)[:160])
            def is_due():       # пауза — от КОНЦА прошлого пересчёта: между пересчётами сервер свободен для ответов поиска
                age = time.time() - max(state.get("built_at", 0), state.get("built_end", 0))
                return bool((state.get("dirty_new") and age >= 15) or (state.get("dirty") and age >= 180))
            due = is_due()
            # ленту уже пересчитал кто-то другой (★, «Купил»…) — осталось разослать уведомления
            done_by_other = state.get("after_build") and not state.get("dirty_new")
            if not (due or done_by_other):
                continue
            try:
                with lock:
                    if due and is_due():    # пока ждали замок, ленту мог пересчитать кто-то другой (★, каталог, курс)
                        rebuild()
                    jobs, state["after_build"] = state.get("after_build", []), []
                    for items, old_prices, q, is_search, added in jobs:
                        if is_search:
                            trial_note(q, len(items), sum(1 for i in items if i in state.get("by_id", {})), added)
                        push_alerts(items, old_prices, q)
            except Exception as e:
                say("Фоновый пересчёт ленты:", repr(e)[:160])
    try:
        import ai_check
        ai_check.load_settings()      # создаёт settings.json с пустым ключом при первом запуске
        state["checker"] = ai_check.Checker(db, lock, ai_done)
        say("Проверка фото ИИ: " + ("включена" if ai_settings().get("gemini_api_key") else
                                      "выключена — впишите ключ Gemini в settings.json"))
    except Exception as e:
        say("Проверка фото ИИ недоступна:", repr(e)[:150])
    boot_file = outdir / "boot.json"       # сколько занял прошлый запуск — шкала заставки идёт по этому времени
    try:
        prev = json.loads(boot_file.read_text(encoding="utf-8"))
    except Exception:
        prev = {}
    state["boot_eta"] = float(prev.get("sec") or 0)
    state["boot_prev"] = {k: int(prev.get(k) or 0) for k in ("rows", "good", "top")}      # итоги прошлого запуска — для счётчиков заставки
    try:
        state["boot_rows"] = db.execute("SELECT COUNT(*) FROM items WHERE arch = 0").fetchone()[0]
    except Exception:
        state["boot_rows"] = 0

    def first_build():
        """Первый пересчёт ленты — в фоне: окно открывается сразу и показывает заставку, а не ждёт пустым."""
        import gp_cache
        t0, got = time.time(), {}
        try:
            with lock:
                if refresh_market_sell(db, cat):     # цены рынка России изменились с прошлого сеанса — переоценка, как в rebuild()
                    _EVAL_CACHE.clear()
                    unarchive_recent(db)
                try:        # память оценок прошлого сеанса: годится, пока не менялись код, каталог, расходы, курс и цены рынка
                    got = gp_cache.load(outdir, a, cat, prices)
                except Exception as e:
                    say("память оценок не прочитана:", repr(e)[:200])
                rebuild()
        except Exception as e:
            say("ошибка первого пересчёта ленты:", repr(e)[:300])
        state["ready"] = True
        say("Память оценок с прошлого запуска: " + (", ".join(f"{k} {v}" for k, v in got.items() if v) or "не подошла, считаю заново"))
        sec = round(time.time() - state["started"], 1)
        say(f"Запуск: лента готова через {sec} с (пересчёт {time.time() - t0:.1f} с)")
        try:
            boot_file.write_text(json.dumps({"sec": sec, "rows": state.get("boot_rows", 0), "good": state.get("good", 0),
                                             "top": state.get("top", 0)}), encoding="utf-8")
        except OSError:
            pass
        for bg in (rates_bg, phash_bg, phone_bg, chan_bg, lazy_bg):
            threading.Thread(target=bg, daemon=True).start()

        def cache_bg():     # память оценок — на диск: вскоре после запуска и дальше раз в полчаса; следующий запуск будет быстрым
            pause = 20
            while True:
                time.sleep(pause)
                pause = 1800
                try:
                    held = gp_cache.save(outdir, a, cat, prices, lock)
                    if held > 5:
                        say(f"память оценок сохранялась {held:.1f} с — дольше обычного")
                except Exception as e:
                    say("память оценок не сохранена:", repr(e)[:200])
        threading.Thread(target=cache_bg, daemon=True).start()
    threading.Thread(target=first_build, daemon=True).start()

    def stats_bg():
        """«Аналитика» (воронка, часы публикаций) — два прохода по всей базе. Считаем в фоне, своим соединением
        только для чтения и не чаще раза в 3 минуты: страница получает готовый ответ мгновенно, пересчёт ленты не ждёт."""
        import sqlite3
        path = db.execute("PRAGMA database_list").fetchone()[2]
        last = None
        while True:
            try:
                if state.get("built_at") and state.get("built_at") != last and "by_id" in state:
                    ro = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
                    try:
                        state["stats"] = stats_data(ro, state)
                    finally:
                        ro.close()
                    last = state.get("built_at")
                    time.sleep(170)
            except Exception as e:
                say("Аналитика: ошибка", repr(e)[:160])
                time.sleep(60)
            time.sleep(10)
    threading.Thread(target=stats_bg, daemon=True).start()
    threading.Thread(target=backup_bg, daemon=True).start()
    global _server
    class QuietServer(ThreadingHTTPServer):
        """Страница закрыла соединение, пока шёл ответ, — это не ошибка: без трассировки в журнале."""
        def handle_error(self, request, client_address):
            e = sys.exc_info()[1]
            if not isinstance(e, (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)):
                say(f"ошибка запроса от {client_address[0]}: {e!r}", level=40)

    server = _server = QuietServer(("127.0.0.1", 8765), Handler)
    say("Жду данные от расширения. Листайте поиск Goofish в обычном Chrome.")
    say("Живой отчёт: http://127.0.0.1:8765/  (или щелчок по значку расширения). Остановка: Ctrl+C.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        say("\nОстановлено. Всё собранное сохранено в базе.")
    finally:
        server.server_close()


_server = None




def splash_page(eta: float = 0, prev: dict | None = None, min_sec: float = 4.5) -> str:
    """Заставка с анимацией (ui/splash.html): показывается, пока идёт первый пересчёт ленты, не меньше min_sec секунд."""
    try:
        s = (Path(__file__).with_name("ui") / "splash.html").read_text(encoding="utf-8")
    except OSError:
        s = "<meta charset=utf-8><body style='background:#0b0e0d;color:#e7ebe8;font:16px Segoe UI'>Sheben'Store запускается…" \
            "<script>setInterval(()=>fetch('/boot').then(r=>r.json()).then(j=>j.ready&&location.reload()),800)</script>"
    return (s.replace("__ETA__", str(round(float(eta or 0), 1))).replace("__MIN__", str(min_sec))
            .replace("__PREV__", json.dumps(prev or {})))


def save_settings_keys(**kw):
    """Дописывает поля в settings.json, не трогая остальное. Секреты (ключ телефона, чат бота…) уходят в .env,
    в settings.json от них остаётся пустое поле."""
    import ai_check
    import config
    try:
        s = json.loads(ai_check.SETTINGS.read_text(encoding="utf-8"))
    except Exception:
        s = {}
    config.save_secrets({k: v for k, v in kw.items() if k in config.SECRET_KEYS})
    s.update({k: ("" if k in config.SECRET_KEYS else v) for k, v in kw.items()})
    ai_check.SETTINGS.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")


def stop_listen():
    """Остановка --listen из другого потока (значок в трее app.pyw)."""
    if _server:
        _server.shutdown()
# ---------------------------------------------------------------- CLI
def build_args(argv: list[str] | None = None):
    ap = argparse.ArgumentParser(description="Goofish parser PRO")
    g = ap.add_argument_group("запросы и охват")
    g.add_argument("-q", "--query", action="append", default=[], help="поисковый запрос (можно несколько)")
    g.add_argument("--queries-file", help="файл с запросами, по одному на строку")
    g.add_argument("--preset", choices=["prada", "prada-wide", "prada-grail", "prada-grail-plus"], help="готовый набор запросов")
    g.add_argument("--brand", choices=["both", "cn", "en"], default="both",
                   help="для --preset: писать бренд на китайском, английском или обоими способами (both вдвое больше запросов)")
    g.add_argument("--paste", action="append", default=[],
                   help="файл с текстом выдачи, скопированным из обычного браузера (Ctrl+A, Ctrl+C); можно несколько")
    g.add_argument("--listen", action="store_true",
                   help="принимать объявления от расширения chrome_ext, пока вы листаете Goofish в обычном Chrome")
    g.add_argument("--html-refresh", type=int, default=20,
                   help="для --listen: как часто файл out/best.html сам обновляется, сек (0 = не обновлять); "
                        "живой отчёт http://127.0.0.1:8765/ обновляется кнопкой")
    g.add_argument("--pages", type=int, default=3, help="страниц/прокруток на запрос")
    g.add_argument("--delay", type=float, default=3.0, help="базовая пауза, сек (не ставьте меньше 2)")
    g.add_argument("--login-wait", type=int, default=180, help="сколько ждать входа/проверки, сек")
    g.add_argument("--load-timeout", type=int, default=120, help="сколько ждать открытия страницы, сек")
    g.add_argument("--manual-retries", type=int, default=2,
                   help="сколько раз за запрос дать вам пройти проверку вручную и повторить")
    g.add_argument("--auto-close", action="store_true",
                   help="закрывать браузер сразу после работы (по умолчанию окно остаётся открытым до нажатия Enter)")

    f = ap.add_argument_group("фильтры")
    f.add_argument("--min-price", type=float, default=None,
                   help="один порог цены в ¥ для всего (ниже = категория B); по умолчанию свой для бренда и типа из catalog.json")
    f.add_argument("--brands", action="append", default=[],
                   help="только эти бренды, ключи из catalog.json через запятую (prada,golden_goose)")
    f.add_argument("--types", action="append", default=[],
                   help="только эти типы вещей, ключи категорий из catalog.json через запятую (bag,sneakers)")
    f.add_argument("--grail", action="store_true",
                   help="оставлено для совместимости: теперь всегда ставится общая оценка из 5 частей")
    f.add_argument("--only-new", action="store_true",
                   help="показывать только новые объявления и те, где цена снизилась (по сравнению с прошлыми запусками)")
    f.add_argument("--db", default=None, help="база объявлений (по умолчанию out/items.db)")
    f.add_argument("--reset-seen", action="store_true", help="начать базу заново (старая сохранится как .bak)")
    f.add_argument("--no-low-tier", action="store_true",
                   help="в режиме --grail не искать категорию B (низкая цена с сигналами)")
    f.add_argument("--low-floor", type=float, default=None,
                   help="категория B: абсолютный минимум цены, ¥ (по умолчанию из catalog.json по типу вещи)")
    f.add_argument("--low-min", type=int, default=3, help="категория B: мин. число баллов сигналов (сильный=2, обычный=1)")
    f.add_argument("--require-rarity", action="store_true", help="оставлять только с признаками редкости")
    f.add_argument("--max-price", type=float, default=0, help="максимум в ¥ (0 = без лимита)")
    f.add_argument("--city", action="append", default=[], help="только эти города (кит.), можно несколько")
    f.add_argument("--exclude-city", action="append", default=[], help="исключить города")
    f.add_argument("--must", action="append", default=[], help="обязательное слово в названии/описании")
    f.add_argument("--exclude-word", action="append", default=[], help="доп. слова-исключения")
    f.add_argument("--min-want", type=int, default=0, help="мин. число 'хотят'")
    f.add_argument("--free-shipping", action="store_true", help="только с бесплатной доставкой по Китаю")
    f.add_argument("--require-bag", action="store_true",
                   help="то же, что --types bag")
    f.add_argument("--require-model", action="store_true", help="оставлять только распознанные модели")
    f.add_argument("--only-profitable", action="store_true", help="убрать убыточные по расчёту")

    e = ap.add_argument_group("расчёт выгодности (значения по умолчанию из вашего калькулятора)")
    e.add_argument("--prices", help="json: модель -> ожидаемая цена продажи, ₽")
    e.add_argument("--default-sell", type=float, default=0, help="цена продажи по умолчанию, ₽")
    e.add_argument("--rate", type=float, default=12.8981, help="курс ¥→₽")
    e.add_argument("--cn-ship", type=float, default=0, help="доставка по Китаю, ¥")
    e.add_argument("--intl", type=float, default=884, help="международная доставка, ₽/посылка")
    e.add_argument("--ru", type=float, default=1268.54, help="доставка по РФ, ₽/посылка")
    e.add_argument("--photo", type=float, default=150, help="фотоотчёт и сборы, ₽")
    e.add_argument("--prot", type=float, default=0.0086, help="защита от рисков, доля от стоимости")
    e.add_argument("--duty", type=float, default=0.0, help="пошлины, доля от стоимости")
    e.add_argument("--expert", type=float, default=50, help="экспертиза, ¥")
    e.add_argument("--comm", type=float, default=0.20, help="комиссия площадки продажи, доля")
    e.add_argument("--tax", type=float, default=0.0, help="налог, доля от выручки")
    e.add_argument("--other", type=float, default=0.03, help="прочее/возвраты, доля от цены")
    e.add_argument("--target", type=float, default=0.25, help="целевая маржа, доля")

    d = ap.add_argument_group("прочее")
    d.add_argument("--details", action="store_true", help="открыть карточки топ-кандидатов и проверить описание")
    d.add_argument("--details-limit", type=int, default=30)
    d.add_argument("--max-api-calls", type=int, default=120,
                   help="максимум запросов поиска за один запуск (защита аккаунта)")
    d.add_argument("--cooldown-hours", type=float, default=6,
                   help="пауза после сигнала ограничения от сайта; в это время запуск блокируется")
    d.add_argument("--ignore-cooldown", action="store_true", help="не рекомендуется: игнорировать паузу")
    d.add_argument("--profile", default="./profile")
    d.add_argument("--out", default="out")
    d.add_argument("--catalog", default=str(Path(__file__).with_name("catalog.json")),
                   help="каталог брендов и категорий (по умолчанию catalog.json рядом со скриптом)")
    d.add_argument("--headless", action="store_true", help="без окна (вход может не пройти)")
    d.add_argument("--chrome", action="store_true",
                   help="запускать установленный на ПК Google Chrome вместо встроенного Chromium (профиль всё равно отдельный, из --profile)")
    d.add_argument("--disable-sandbox", action="store_true",
                   help="запасной вариант: выключить песочницу браузера, если без неё не запускается (снижает безопасность)")
    d.add_argument("--minimized", action="store_true", help="запустить окно браузера свёрнутым (удобно для работы в фоне)")
    d.add_argument("--debug", action="store_true", help="сохранить сырые ответы API")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None):
    a = build_args(argv)
    queries = list(a.query)
    if a.queries_file:
        queries += [l.strip() for l in Path(a.queries_file).read_text(encoding="utf-8").splitlines() if l.strip()]
    if a.preset:
        queries += build_preset(a.preset, a.brand)
    queries = list(dict.fromkeys(queries))
    a.low_tier = not a.no_low_tier
    a.brands = {low(x).strip() for v in a.brands for x in v.split(",") if x.strip()}
    a.types = {x.strip() for v in a.types for x in v.split(",") if x.strip()}
    if a.require_bag:
        a.types.add("bag")
    if not queries and not a.paste and not a.listen:
        sys.exit("Нет запросов: укажите --listen, --paste файл.txt, -q, --queries-file или --preset")

    cat = load_catalog(Path(a.catalog))
    unknown = a.types - set(cat["categories"])
    if unknown:
        sys.exit(f"Нет таких категорий в каталоге: {', '.join(unknown)}. Есть: {', '.join(cat['categories'])}")
    prices = {}
    if a.prices:
        prices = json.loads(Path(a.prices).read_text(encoding="utf-8"))

    outdir = Path(a.out)
    outdir.mkdir(exist_ok=True)
    if not a.paste and not a.listen:
        say(f"Запросов: {len(queries)}, страниц на запрос: {a.pages}")
        left = cooldown_left(outdir / "cooldown.json")
        if left > 0 and not a.ignore_cooldown:
            sys.exit(f"Сайт недавно сообщил об ограничении. Подождите ещё около {left:.1f} ч и запустите снова.")
    session_start = datetime.now().isoformat(timespec="seconds")
    db = open_db(Path(a.db) if a.db else outdir / "items.db", outdir, a.reset_seen)

    if a.listen:
        listen(a, cat, prices, outdir, db, session_start)
        return

    if a.paste:
        a.details = False
        items = {}
        for fn in a.paste:
            ptxt = read_text_auto(Path(fn))
            part = parse_paste_text(ptxt, Path(fn).name)
            say(f"{fn}: разобрано объявлений: {len(part)}")
            if not part:
                describe_paste_problem(Path(fn), ptxt)
            for k, v in part.items():
                items.setdefault(k, v)
        if not items:
            sys.exit("Не удалось разобрать ни одного объявления: проверьте, что вставлен текст страницы выдачи (с ценами).")
    else:
        items = asyncio.run(collect(a, queries, outdir))
        if not items:
            sys.exit("Ничего не собрано: проверьте вход в аккаунт или запустите с --debug.")
    ingest(db, items, session_start)
    rows = load_rows(db, session_start)
    good, bad, reasons = process(rows, a, cat, prices, outdir)

    if a.details and good:
        top = good[: a.details_limit]
        say(f"Проверяю описание у {len(top)} лучших кандидатов...")
        asyncio.run(fetch_details(a, top, outdir))
        for r in top:
            if r.get("desc"):
                update_fields(db, r["id"], desc=r["desc"])
        rows = load_rows(db, session_start)
        good, bad, reasons = process(rows, a, cat, prices, outdir)

    n_new = sum(1 for r in rows if r["status"] == "новое")
    n_drop = sum(1 for r in rows if r["status"] == "цена снижена")
    say(f"\nВ этом запуске: {len(items)}; новых: {n_new}; со сниженной ценой: {n_drop}")
    say(f"Всего в базе: {len(rows)}; прошло фильтры: {len(good)}; отсеяно: {len(bad)}")
    for k, v in sorted(reasons.items(), key=lambda x: -x[1]):
        say(f"  - {k}: {v}")
    say(f"Файлы в папке {outdir}/: best.html (откройте двойным щелчком), best.csv, all_items.csv, rejected.csv")


if __name__ == "__main__":
    main()
