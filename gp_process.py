"""Подделки (фото, продавцы реплик, шаблонный текст) и пересчёт ленты process()."""
from __future__ import annotations

from gp_pages import *  # noqa: F401,F403


def _photo_key(url: str) -> str:
    """Имя файла фото на alicdn (O1CN…): одно и то же фото у разных продавцов = чужие/поставщицкие снимки."""
    m = re.search(r"(O1CN[0-9A-Za-z_]+)", url or "")
    return m.group(1) if m else ""


NEW_WORDS = ["全新", "未使用", "未拆", "吊牌", "新款", "最新款"]
PACK_WORDS = ["全套", "配件齐全", "礼盒", "手提袋", "专柜包装", "包装盒", "防尘袋", "盒子", "购物袋", "原包装", "包装齐全"]
# косметика/парфюм/дом люксовых брендов (лак Dior за ¥129) — реально дешёвые, по ним продавца не судим
BEAUTY_WORDS = ["指甲油", "香水", "淡香", "浓香", "香氛", "香薰", "蜡烛", "口红", "唇膏", "唇釉", "粉底", "蜜粉", "腮红", "眼影",
                "睫毛", "眉笔", "化妆", "彩妆", "护肤", "面霜", "精华", "乳液", "爽肤", "洗发", "发香", "喷雾", "小样",
                "试用", "ml", "擦银布", "护膝", "瑜伽"]
_LUX_CACHE: dict = {}


def seller_key(r: dict) -> str:
    return r.get("seller_id") or ("nick:" + r["seller"] if r.get("seller") else "")


def _cheap_new_lux(r: dict, cat: dict) -> str:
    """«Prada ¥800», если вещь — новая люксовая без 验货宝 дешевле 3.5 минимумов бренда; иначе ""."""
    key = (r.get("id"), r.get("title"), r.get("desc"), r.get("labels"), r.get("price_cny"), r.get("tags"), r.get("site"))
    if key in _LUX_CACHE:
        return _LUX_CACHE[key]
    res = ""
    title, desc = low(r.get("title", "")), low(r.get("desc", "") + " " + r.get("labels", ""))
    text = title + " | " + desc
    # Mercari: 验货宝 там нет, новых оригиналов дёшево больше — порог мягкий, как у вещи с 验货宝
    k_new = 2.0 if "验货宝" in r.get("tags", "") or r.get("site") == "mercari" else 3.5
    if has_any(text, NEW_WORDS) and not has_any(title, BEAUTY_WORDS + NON_FASHION):
        ckey = detect_category(title, desc, cat)
        floor_c = cat["categories"].get(ckey, {}).get("min_price") or 500
        bkey, _ = detect_brand(title, text, cat)
        if bkey:
            b = cat["brands"][bkey]
            mp = b.get("min_price", {})
            name, floor = b["name"], mp.get(ckey, mp.get("*", floor_c))
        else:
            hit = outside_brand(text, cat)
            name = cat["outside"]["interesting"][hit[2]]["name"] if hit and hit[1] == 1 else ""
            floor = (cat.get("outside") or {}).get("min_price", 500)
        if name and r.get("price_cny", 0) < floor * k_new:
            res = f"{name} ¥{r['price_cny']:.0f}"
    _LUX_CACHE[key] = res
    return res


def seller_blacklist(rows: list[dict], cat: dict) -> dict[str, dict]:
    """Продавцы реплик. Как вы проверяете вручную через профиль: у продавца сразу несколько «новых»
    люксовых вещей по цене б/у (Dior за ¥1200, Prada за ¥800) — или вы уже дважды отметили его вещи подделкой."""
    by: dict[str, list] = {}
    for r in rows:
        k = seller_key(r)
        if k:
            by.setdefault(k, []).append(r)
    out = {}
    for k, rs in by.items():
        fakes = sum(1 for r in rs if r.get("mark_reason") == "подделка")
        cheap = [c for c in (_cheap_new_lux(r, cat) for r in rs) if c]
        brands = {c.rsplit(" ¥", 1)[0] for c in cheap}
        why = ""
        if fakes >= 2:
            why = f"вы отметили {fakes} его вещи как подделку"
        elif len(brands) >= 2 or len(cheap) >= 3:
            why = "новые люксовые вещи по цене б/у: " + ", ".join(cheap[:4])
        if why:
            out[k] = {"why": why, "seller": rs[0].get("seller", ""), "n": len(rs),
                      "profile": f"https://www.goofish.com/personal?userId={k}" if not k.startswith("nick:") else ""}
    return out


# Отпечатки фото (dHash 64 бита по миниатюре): ловят пересохранённые и обрезанные копии одного снимка,
# которые по имени файла не совпадают. Заполняет фоновый поток в listen(); id объявления -> отпечаток.
PHASH_MAX_DIST = 3      # отличий в битах; 4 полосы по 16 бит — при ≤3 отличиях одна полоса совпадёт точно


def dhash(img) -> int:
    px = list(img.convert("L").resize((9, 8)).getdata())
    return sum(1 << i for i, (a_, b_) in enumerate((px[r * 9 + c], px[r * 9 + c + 1]) for r in range(8) for c in range(8)) if a_ > b_)


def similar_photo_sellers(good: list[dict]) -> dict[str, set]:
    """id -> продавцы ДРУГИХ объявлений с похожим фото."""
    hs = [(r["id"], PHASH[r["id"]], r.get("seller") or r["id"]) for r in good if r["id"] in PHASH]
    hs = [x for x in hs if 8 <= bin(x[1]).count("1") <= 56]      # однотонные картинки похожи на всё
    bands: dict = {}
    for i, (_, h, _) in enumerate(hs):
        for b in range(4):
            bands.setdefault((b, (h >> (16 * b)) & 0xFFFF), []).append(i)
    out: dict[str, set] = {}
    for idx in bands.values():
        if len(idx) < 2 or len(idx) > 60:
            continue
        for i in idx:
            for j in idx:
                if i < j and hs[i][2] != hs[j][2] and bin(hs[i][1] ^ hs[j][1]).count("1") <= PHASH_MAX_DIST:
                    out.setdefault(hs[i][0], set()).add(hs[j][2])
                    out.setdefault(hs[j][0], set()).add(hs[i][2])
    return out


TEXT_SIM_MIN = 0.5      # доля общих трёхбуквенных кусков описания (Жаккар) — «один шаблон у разных продавцов»
TEXT_POST_MAX = 300     # кусок, который есть у 300+ объявлений («正品», «闲置»), для поиска кандидатов бесполезен
# память между пересчётами: каждое объявление сравнивается один раз — когда появилось (или сменило текст)
_TS: dict = {"docs": {}, "post": {}, "out": {}}


def similar_text_sellers(rows: list[dict]) -> dict[str, set]:
    """id -> продавцы ДРУГИХ объявлений с почти тем же описанием (сети реплик копируют один текст).
    Смотрим по всей базе, не только по ленте: «братья» по шаблону часто уже отсеяны."""
    docs, post, out = _TS["docs"], _TS["post"], _TS["out"]
    for r in rows:
        t = re.sub(r"[\W_\d]+", "", low(r.get("desc") or r.get("title") or ""))[:120]
        if len(t) < 25 or docs.get(r["id"], ("", "", None))[1] == t:
            continue
        sh = frozenset(t[i:i + 3] for i in range(len(t) - 2))
        me = r.get("seller_id") or r.get("seller") or r["id"]
        shared: Counter = Counter()
        for g in sh:
            ids = post.setdefault(g, [])
            if len(ids) < TEXT_POST_MAX:
                shared.update(ids)      # счёт внутри Counter идёт в C — при первом пересчёте это секунды
            ids.append(r["id"])
        thr = len(sh) * TEXT_SIM_MIN / 2   # при Жаккаре ≥ 0,5 общих кусков не меньше трети от суммы — грубый отсев
        for j in [j for j, n in shared.items() if n >= thr and j != r["id"]]:
            other, _, osh = docs[j]
            if other != me and len(sh & osh) / len(sh | osh) >= TEXT_SIM_MIN:
                out.setdefault(r["id"], set()).add(other)
                out.setdefault(j, set()).add(me)
        docs[r["id"]] = (me, t, sh)
    return out


def fake_risk(good: list[dict], cat: dict, sim_text: dict | None = None):
    """Риск подделки по совокупности признаков. Это не экспертиза: подлинность так не доказать,
    но так видно, какие объявления проверять в первую очередь, а какие отложить."""
    sim_text = sim_text or {}
    sim = similar_photo_sellers(good)
    sellers, photos = {}, {}
    for r in good:
        if r.get("seller"):
            sellers.setdefault((r["seller"], r.get("brand", "")), []).append(r)
        k = _photo_key(r.get("image", ""))
        if k:
            photos.setdefault(k, set()).add(r.get("seller") or r["id"])
    for r in good:
        text = low(f'{r.get("title", "")} {r.get("desc", "")} {r.get("tags", "")}')
        pts, why = 0, []
        soft = has_any(text, RISK_WORDS)
        if soft:   # одно слово — слабый признак (+1), несколько — сильнее
            pts += min(len(soft), 4) if len(soft) >= 2 else 1
            why.append("продающие слова реплик: " + ru_words(soft[:3]))
        if "новое и дёшево" in str(r.get("red_flags", "")):
            pts += 3
            why.append("новое, а цена как у б/у — для оригинала слишком дёшево")
        pvm = r.get("price_vs_median")
        if pvm != "" and pvm is not None and float(pvm) < 0.5:
            pts += 2
            why.append(f"цена ×{pvm} от медианы")
        same = sellers.get((r.get("seller"), r.get("brand", "")), [])
        new_same = [x for x in same if (x.get("s_cond") or 0) >= NEW_COND]
        if len(same) >= 4 and len(new_same) >= 3:
            pts += 3
            why.append(f"у продавца {len(same)} вещей {r.get('brand', '')}, из них новых {len(new_same)}")
        k = _photo_key(r.get("image", ""))
        r["same_photo"] = len(photos.get(k, ())) if k and len(photos.get(k, ())) >= 2 else ""
        if r["same_photo"]:
            pts += 3
            why.append(f"это же фото у {len(photos[k])} продавцов")
        r["sim_photo"] = len(sim.get(r["id"], ())) + 1 if not r["same_photo"] and sim.get(r["id"]) else ""
        if r["sim_photo"]:
            pts += 3
            why.append(f"похожее фото ещё у {r['sim_photo'] - 1} продавц.: снимок мог быть взят чужой")
        r["sim_text"] = len(sim_text.get(r["id"], ())) or ""
        if r["sim_text"]:
            pts += 3
            why.append(f"тот же текст ещё у {r['sim_text']} продавц.: так пишут сети реплик")
        ai = r.get("ai") or {}
        if ai.get("fake_risk") in ("high", "medium"):
            pts += 3 if ai["fake_risk"] == "high" else 1
            signs = "; ".join(ai.get("fake_signs", [])[:2])
            why.append(f"ИИ по фото: риск {'высокий' if ai['fake_risk'] == 'high' else 'средний'}" + (f" ({signs})" if signs else ""))
        elif ai.get("fake_risk") == "low":
            pts -= 1
        if ai.get("studio_photo") and ai.get("packaging_shown"):
            pts += 1
            why.append("ИИ: витринная съёмка с упаковкой")
        shop = has_any(low(r.get("seller", "")), SHOP_NICK_WORDS)
        if shop:
            pts += 2
            why.append(f"ник продавца как у магазина («{r['seller']}»)")
        is_new = (r.get("s_cond") or 0) >= NEW_COND
        pack = has_any(text, PACK_WORDS)
        if is_new and len(pack) >= 2:
            pts += 2
            why.append("новая и с полным комплектом упаковки — так снимают витринные реплики")
        if is_new and (text.count("#") >= 3 or re.search(r"[\U0001F300-\U0001FAFF✅❌❗⭐]", text)):
            pts += 1
            why.append("оформлено как у магазина (хэштеги, значки)")
        if not is_new and has_any(text, ["vintage", "archive", "中古", "古着"]):
            pts -= 1    # ношеный архив/винтаж: реплики так не продают
            why.append("плюс: ношеный винтаж/архив")
        proof = has_any(text, PROOF_WORDS)
        if proof:   # 验货宝 — значок сервиса, его ставят и продавцы реплик: только −1, как чек
            pts -= 1
            why.append("плюс: " + ru_words(proof[:2]))
        level = "высокий" if pts >= 4 else "средний" if pts >= 2 else "низкий"
        r.update(fake_risk=level, risk_why="; ".join(why))
        if level != "низкий" and r.get("deal_bonus"):     # дёшево и подозрительно — не «выгодно», а «проверить»
            r.update(deal_pct="", deal_bonus=0)
            r["total"] = total_score(r, cat)
        if level == "высокий":
            r["s_trust"] = min(r.get("s_trust", 50), 30)
            r["tier"] = "B"          # не «рыночная цена», а «проверить»: в категорию A подозрительные не попадают
            r["risk_penalty"] = r.get("risk_penalty", 0) + 10
            r["total"] = total_score(r, cat)


def process(rows: list[dict], a, cat: dict, prices: dict, outdir: Path, refresh: int = 0):
    good, bad = [], []
    reasons: dict[str, int] = {}
    for row in rows:
        try:
            ok, why = evaluate_cached(row, a, cat, prices)
        except Exception as e:      # одна сломанная вещь не должна останавливать пересчёт остальных
            parse_error(str(row.get("id", "?")), repr(e), "оценка")
            ok, why = False, "ошибка оценки"
        if ok:
            good.append(row)
        else:
            row["reject"] = why
            bad.append(row)
            key = why.split(":")[0].split(" (")[0]
            reasons[key] = reasons.get(key, 0) + 1
    # правила из ваших скрытий «не оправдывает цену» / «неликвидный размер»; ★ не трогаем
    LEARNED["rules"] = learned_rules(rows, a, cat, prices)
    rules = [x for x in LEARNED["rules"] if x["key"] not in LEARNED["off"]]
    if rules:
        keep = []
        for r in good:
            why = "" if r.get("mark") == "like" else rule_hit(r, rules)
            if why:
                r["reject"] = why
                bad.append(r)
                reasons[why.split(":")[0]] = reasons.get(why.split(":")[0], 0) + 1
            else:
                keep.append(r)
        good[:] = keep
    # «рынок Goofish»: цена относительно медианы своей группы — модель, если известна, иначе бренд + тип вещи.
    # Медиана — по вещам уровня A (дешёвые «проверить» её не тянут вниз), нужно хотя бы DEAL_MIN_N объявлений.
    # 0,35–0,7 медианы — «ниже рынка», плюс к оценке; ещё дешевле — продавец не знает цены, но и подделка тоже.
    import statistics
    groups: dict[tuple, list[dict]] = {}
    for r in good:
        r.update(price_vs_median="", deal_pct="", deal_n="", deal_med="", deal_bonus=0)
        key = (r["brand"], "m", r["model"]) if r.get("model") else (r["brand"], "c", r["category"])
        groups.setdefault(key + (r.get("site", ""),), []).append(r)     # Япония — свой рынок, медиана отдельно
    for rs in groups.values():
        base = [x["price_cny"] for x in rs if x.get("tier") == "A"]
        if len(base) < DEAL_MIN_N:
            continue
        med = statistics.median(base)
        if not med:
            continue
        for x in rs:
            v = round(x["price_cny"] / med, 2)
            x.update(price_vs_median=v, deal_n=len(base), deal_med=round(med))
            # сумки подделывают больше всего: у них и «−50% к рынку» — уже скорее реплика, чем удача
            if v < 0.35 or (x.get("ckey") == "bag" and v < 0.5):
                x["s_trust"] = clamp(x["s_trust"] - 10)
            elif v <= 0.7:
                x.update(deal_pct=round(100 * (1 - v)), deal_bonus=min(8, max(1, round((0.75 - v) * 20))))
            x["total"] = total_score(x, cat)
    # спрос на Goofish: «хотят» в день с момента публикации; медиана по той же группе (модель или бренд + тип), если
    # объявлений с известной датой ≥ DEM_MIN_N. Поправка к ликвидности — относительно среднего спроса по всей ленте:
    # вдвое ходовее среднего +10, вдвое хуже −10 (не больше ±15), так что вся лента не сдвигается
    now = datetime.now()
    dem: dict[tuple, list[float]] = {}
    for key, rs in groups.items():
        for x in rs:
            x.update(dem_day="", dem_med="", dem_n="", dem_adj="")
            if x.get("site") == "mercari":      # «хотят» Goofish и лайки Mercari несравнимы — спрос не считаем
                continue
            try:
                age = (now - datetime.fromisoformat(x.get("posted") or "")).total_seconds() / 86400
            except ValueError:
                continue
            x["dem_day"] = round(x.get("want", 0) / max(age, 0.5), 2)
            dem.setdefault(key, []).append(x["dem_day"])
    every = [d for ds in dem.values() for d in ds]
    base = statistics.median(every) if len(every) >= 3 * DEM_MIN_N else 0
    for key, ds in dem.items():
        if len(ds) < DEM_MIN_N or not base:
            continue
        med = statistics.median(ds)
        adj = max(-15, min(15, round(10 * math.log2(max(med, base / 8) / base))))
        for x in groups[key]:
            x.update(dem_med=round(med, 1), dem_n=len(ds), dem_adj=adj, s_liq=clamp(x["s_liq"] + adj))
            x["total"] = total_score(x, cat)
    fake_risk(good, cat, similar_text_sellers(rows))
    # строгий отбор подделок: продавцы реплик, высокий риск и чужие фото — в «Отсеяно», средний риск — в B
    bl = seller_blacklist(rows, cat)
    SELLER_BLACKLIST.clear()
    SELLER_BLACKLIST.update(bl)
    # продавец из профиля (по номеру) и тот же продавец в поиске (только по нику) — одно лицо
    by_nick = {s["seller"]: s for s in bl.values() if s["seller"] and not s["seller"].startswith("продавец ")}
    keep = []
    for r in good:
        k = seller_key(r)
        if k not in bl and r.get("seller") in by_nick:
            bl[k] = by_nick[r["seller"]]
        nick = has_any(low(r.get("seller", "")), SHOP_NICK_STRONG)
        why = ("продавец реплик: " + bl[k]["why"] if k in bl
               else f"продавец реплик: ник «{r['seller']}» ({nick[0]})" if nick
               else "вероятная подделка: " + r["risk_why"] if r.get("fake_risk") == "высокий"
               else f"чужое фото: то же фото у {r['same_photo']} продавцов" if r.get("same_photo") else "")
        # Mercari: с посредником вещь должна окупаться (порог — «Сделки», «В ленту — от прибыли»); ★ остаётся
        p = r.get("profit_rub")
        if not why and r.get("site") == "mercari" and r.get("mark") != "like" and isinstance(p, int) \
                and p < COSTS.get("jp_feed_min", 0):
            why = "не окупается с посредником: прибыль " + f"{p:,} ₽".replace(",", " ")
        if why:
            r["reject"] = why
            bad.append(r)
            head = why.split(":")[0]
            reasons[head] = reasons.get(head, 0) + 1
            continue
        if r.get("fake_risk") == "средний":
            r["tier"] = "B"
        keep.append(r)
    good[:] = keep
    for r in good:
        r["has_desc"] = 1 if r.get("desc") else ""
        ai = r.get("ai") or {}
        r["ai_txt"] = ("; ".join(x for x in (ai.get("item", ""), {"high": "риск подделки высокий", "medium": "риск средний",
                                                                   "low": "риск низкий"}.get(ai.get("fake_risk"), ""),
                                             ai.get("verdict", "")) if x) if ai else "")
        r["ai_risk"] = ai.get("fake_risk", "") if ai else ""
        qs = r.get("queries", "")
        r["src"] ="profile" if "профиль" in qs else "rec" if qs == "рекомендации" else "search"
        if r.get("outside") == "интересно" and r.get("fake_risk") == "высокий":
            r["outside"] = "под вопросом"
            r["verdict_why"] += "; высокий риск подделки"
    good.sort(key=lambda r: (-r["total"], r["price_cny"]))
    bad.sort(key=lambda r: r.get("last_seen", ""), reverse=True)
    # файлы CSV/HTML для выгрузки: в приложении (--listen) пишем не чаще раза в 5 минут — это ~0,2 с на каждый пересчёт
    global _FILES_AT
    if not getattr(a, "listen", False) or time.time() - _FILES_AT > 300:
        _FILES_AT = time.time()
        write_csv(outdir / "all_items.csv", rows, ALL_FIELDS)
        write_csv(outdir / "best.csv", good)
        write_csv(outdir / "best_market.csv", [r for r in good if r["tier"] == "A"])
        write_csv(outdir / "best_low.csv", [r for r in good if r["tier"] == "B"])
        write_csv(outdir / "rejected.csv", bad, ["reject", "brand", "category", "title", "price_cny", "city", "url"])
        (outdir / "best.html").write_text(render_html(good, bad, reasons, a.rate, refresh=refresh), encoding="utf-8")
    return good, bad, reasons


CROSS_REJECTS = ("продавец реплик", "вероятная подделка", "чужое фото")     # отсев по соседним вещам, а не по самой вещи


def archive_sweep(db, good: list[dict], bad: list[dict], cat: dict, keep_ids=()) -> dict:
    """Уборка после пересчёта: что больше не нужно оценивать — в архив (см. open_db). База не худеет, худеет работа:
    пересчёт и лента идут только по живым вещам. ★, скрытые вами и купленные не архивируются.
    Возвращает {"stale": давно не попадались, "junk": отсеянные}."""
    today = date.today()
    cut = {s: (today - timedelta(days=d)).isoformat() for s, d in ARCH_DAYS.items()}
    cut_bad = (today - timedelta(days=ARCH_BAD_DAYS)).isoformat()
    todo = []
    for r in good:
        if not r.get("mark") and r["id"] not in keep_ids \
                and (r.get("last_seen") or "9") < cut["mercari" if r.get("site") == "mercari" else ""]:
            todo.append((2, r))
    for r in bad:
        if r.get("mark") or r["id"] in keep_ids:
            continue
        jp = r.get("site") == "mercari"
        seen = r.get("last_seen") or "9"
        if seen < cut["mercari" if jp else ""]:
            todo.append((3, r))
        # отсев по самой вещи: Mercari — сразу (поток ~12 000 в день), Goofish — через ARCH_BAD_DAYS.
        # Улики против продавцов реплик и вещи, отсеянные из-за соседних, живут до общего срока
        elif (jp or seen < cut_bad) and str(r.get("reject", "")).split(":")[0] not in CROSS_REJECTS \
                and not _cheap_new_lux(r, cat):
            todo.append((1, r))
    for arch, r in todo:
        p = r.get("profit_rub")
        qarch_add(db, r, 1, arch == 2, p if isinstance(p, int) else 0)
    if todo:
        db.executemany("UPDATE items SET arch = ? WHERE id = ?", [(arch, r["id"]) for arch, r in todo])
        db.commit()
    return {"stale": sum(1 for arch, _ in todo if arch != 1), "junk": sum(1 for arch, _ in todo if arch == 1)}


_FILES_AT = 0.0
DEAL_MIN_N = 6
DEM_MIN_N = 8       # объявлений с известной датой публикации, чтобы спрос группы считался


def goofish_market(good: list[dict]) -> list[dict]:
    """Таблица «Рынок Goofish» на вкладке «Цены»: по моделям (без модели — бренд + тип) из ленты — сколько объявлений,
    медиана цены, спрос и медиана цен тех, что нашлись 7–21 день назад (тренд появится, когда накопится история)."""
    now = datetime.now()
    wk, lo = (now - timedelta(days=7)).date().isoformat(), (now - timedelta(days=21)).date().isoformat()
    groups: dict[tuple, list[dict]] = {}
    for r in good:
        if r.get("brand") and not r.get("outside"):
            groups.setdefault((r["brand"], r.get("model") or r.get("category", "")), []).append(r)
    med = lambda ps: round(statistics.median(ps)) if len(ps) >= 3 else None
    out = []
    for (b, what), rs in groups.items():
        if len(rs) < 3:
            continue
        out.append({"brand": b, "what": what, "n": len(rs),
                    "med": med([x["price_cny"] for x in rs if x.get("tier") == "A"]) or med([x["price_cny"] for x in rs]),
                    "dem": rs[0].get("dem_med", ""), "dem_n": rs[0].get("dem_n", ""),
                    "now": med([x["price_cny"] for x in rs if x.get("first_seen", "") >= wk]),
                    "old": med([x["price_cny"] for x in rs if lo <= x.get("first_seen", "") < wk])})
    out.sort(key=lambda x: -x["n"])
    return out


__all__ = [n for n in dir() if not n.startswith("__")]   # с «_именами»: модули ниже берут их через *
