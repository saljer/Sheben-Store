"""Бэктест-стенд: насколько прогнозы Щебня сходятся с тем, что произошло.

  python gp_backtest.py            отчёт по out/items.db (читается копия, база не меняется) -> reports/бэктест-ДАТА.md
  python gp_backtest.py --quick    без переоценки вещей (раздел про признаки подделки пропускается)

Главное правило: никакой утечки будущего. Для вещи, появившейся в момент T, прогноз строится только по тому,
что было известно до T (walk_forward). Прогнозы самого Щебня берутся из таблицы snap — они записаны в момент
первого появления вещи и позже не переписываются.

Что считается:
  1. цена объявления: медиана своей группы по более ранним вещам против настоящей цены (MAPE по сегментам);
  2. цена сделки на Mercari: то же по проданным лотам (таблица sold);
  3. цена продажи в России: каталог против постов каналов, Avito, Oskelly и ваших продаж;
  4. топ ленты: чем кончили вещи с высокой оценкой (снижение цены, продажа, ★, скрытие);
  5. признаки подделки: какие флаги чаще стоят у вещей, которые вы отметили «подделка», чем у ★ и у оригиналов
     по экспертизе;
  6. чего не хватает, чтобы выводам можно было верить.
Текущие правила — базовая линия: будущие модели сравниваются с этими цифрами."""
from __future__ import annotations

import json
import shutil
import sqlite3
import statistics
import sys
import tempfile
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MIN_HIST = 3        # сколько более ранних наблюдений нужно группе, чтобы прогнозировать
MIN_SEG = 20        # сегмент с меньшим числом прогнозов в таблицу не выводим: цифра случайна


def walk_forward(obs: list[tuple], min_hist: int = MIN_HIST) -> list[tuple]:
    """obs: (время, ключи_от_точного_к_общему, значение, метка). Идём по времени; прогноз для наблюдения — медиана
    более ранних значений первой группы из ключей, где их набралось min_hist. Само наблюдение попадает в историю
    только после прогноза, наблюдения с тем же временем друг друга не видят.
    -> [(метка, прогноз, факт, ключ, сколько_было_истории)]."""
    hist: dict = defaultdict(list)
    out, pending, last_t = [], [], None
    for t, keys, value, tag in sorted(obs, key=lambda o: o[0]):
        if t != last_t:                 # всё с прошлой отметки времени теперь «прошлое»
            for ks, v in pending:
                for k in ks:
                    hist[k].append(v)
            pending, last_t = [], t
        for k in keys:
            h = hist.get(k)
            if h and len(h) >= min_hist:
                out.append((tag, statistics.median(h), value, k, len(h)))
                break
        pending.append((keys, value))
    return out


def errors(pairs: list[tuple]) -> dict:
    """pairs: (прогноз, факт) -> число, медианная и средняя ошибка в процентах, доля попаданий в ±25 %."""
    ape = [abs(p - f) / f for p, f in pairs if f]
    if not ape:
        return {"n": 0}
    return {"n": len(ape), "mdape": round(100 * statistics.median(ape)), "mape": round(100 * statistics.mean(ape)),
            "in25": round(100 * sum(a <= 0.25 for a in ape) / len(ape))}


def seg_table(res: list[tuple], seg, title: str, top: int = 12) -> list[str]:
    by: dict = defaultdict(list)
    for tag, pred, fact, key, n in res:
        by[seg(tag, key)].append((pred, fact))
    rows = sorted(((k, errors(v)) for k, v in by.items() if len(v) >= MIN_SEG), key=lambda x: -x[1]["n"])[:top]
    if not rows:
        return [f"_{title}: сегментов с {MIN_SEG}+ прогнозами пока нет._", ""]
    out = [f"**{title}**", "", "| Сегмент | Прогнозов | Медианная ошибка | В пределах ±25 % |", "|---|---|---|---|"]
    out += [f"| {k} | {e['n']} | {e['mdape']} % | {e['in25']} % |" for k, e in rows]
    return out + [""]


def ts(s: str) -> float:
    try:
        return datetime.fromisoformat(s).timestamp()
    except (TypeError, ValueError):
        return 0.0


def run(db_path: Path, quick: bool = False) -> str:
    tmp = Path(tempfile.mkdtemp(prefix="sheben_bt_"))
    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    dst = sqlite3.connect(tmp / "items.db")
    src.backup(dst)
    src.close()
    db = dst
    sys.path.insert(0, str(ROOT))
    import goofish_parser_pro as g      # те же правила, что в приложении
    g.open_db(tmp / "items.db", tmp, False).close()      # старая копия: добавит недостающие таблицы
    q = lambda sql, *a: db.execute(sql, a).fetchall()
    L: list[str] = [f"# Бэктест Щебня — {date.today().isoformat()}", ""]

    # переоценка вещей текущими правилами (отметки пользователя убраны); прогнозов ещё нет — дозаписываем в копии
    cat = g.load_catalog(ROOT / "catalog.json")
    good = bad = label = None
    backfilled = 0
    if not quick:
        a = g.build_args(["--listen"])
        a.low_tier, a.brands, a.types = not a.no_low_tier, set(), set()
        g.load_costs()
        a.rate = g.eff_rate()
        gdb = g.open_db(tmp / "items.db", tmp, False)
        gdb.execute("UPDATE items SET arch = 0 WHERE mark != ''")     # отмеченные вещи — в оценку (это копия базы)
        gdb.commit()
        rows = g.load_rows(gdb, "2000-01-01")
        label = {}
        for r in rows:
            if r.get("mark") == "like":
                label[r["id"]] = "★"
            elif r.get("mark") == "hide" and r.get("mark_reason") == "подделка":
                label[r["id"]] = "подделка"
            r.update(mark="", mark_reason="")      # как Щебень видит вещь без вашей подсказки
        for d, st, iid in q("SELECT data, status, item_id FROM deals"):
            x = json.loads(d).get("expert")
            if x in ("оригинал", "подделка"):
                label[iid] = "подделка" if x == "подделка" else "оригинал (экспертиза)"
        good, bad, _ = g.process(rows, a, cat, {}, tmp)
        known = {i for (i,) in gdb.execute("SELECT id FROM snap")}
        backfilled = g.snap_rows(gdb, good, known, "задним числом")     # только в копии: настоящая база не меняется
        gdb.close()

    # ---------------------------------------------------------------- 0. что есть
    n_snap, n_live = q("SELECT COUNT(*), SUM(late = 0) FROM snap")[0]
    n_sold = q("SELECT COUNT(*) FROM sold")[0][0]
    deals = [json.loads(d) | {"status": s} for d, s in q("SELECT data, status FROM deals")]
    n_exp = sum(1 for d in deals if d.get("expert"))
    n_done = sum(1 for d in deals if d["status"] == "продано" and d.get("sold_rub"))
    days = q("SELECT COUNT(*) FROM rates")[0][0]
    span = q("SELECT MIN(first_seen), MAX(first_seen) FROM items")[0]
    L += ["## Что есть для проверки", "",
          f"- Объявлений в базе: {q('SELECT COUNT(*) FROM items')[0][0]:,} за период {str(span[0])[:10]} — {str(span[1])[:10]}.".replace(",", " "),
          f"- Прогнозов Щебня (snap): {n_snap or 0}, из них записано в момент появления вещи: {n_live or 0}"
          + (" — остальные сделаны задним числом, по ним проверяется только цена объявления." if (n_snap or 0) > (n_live or 0) else ".")
          + (f" (В настоящей базе прогнозов ещё не было: {backfilled} дозаписано в копии для этого отчёта.)" if backfilled else ""),
          f"- Проданных лотов Mercari: {n_sold}. Сделок: {len(deals)}, из них продано: {n_done}, с экспертизой: {n_exp}.",
          f"- Дней с записанным курсом: {days}.", ""]

    # ---------------------------------------------------------------- 1. цена объявления
    L += ["## 1. Цена объявления: медиана группы против настоящей цены", "",
          "Для каждой вещи из ленты прогноз — медиана цен более ранних вещей той же модели, а если их мало — того же "
          "бренда и типа вещи (на той же площадке). Так сейчас Щебень считает «рынок» и скидку к нему.", ""]
    obs = []
    for iid, first, site, brand, model, cat_, price in q(
            "SELECT id, first_ts, site, brand, model, category, price FROM snap WHERE price > 0 AND brand != ''"):
        keys = ([("m", site, brand, model)] if model else []) + [("c", site, brand, cat_)]
        obs.append((ts(first), keys, price, (site, brand, cat_)))
    res = walk_forward(obs)
    e_all = errors([(p, f) for _, p, f, _, _ in res])
    if e_all["n"]:
        e_m = errors([(p, f) for _, p, f, k, _ in res if k[0] == "m"])
        e_c = errors([(p, f) for _, p, f, k, _ in res if k[0] == "c"])
        L += [f"Всего прогнозов: {e_all['n']}. Медианная ошибка {e_all['mdape']} %, в пределах ±25 % — {e_all['in25']} % вещей.",
              f"- по модели: {e_m.get('n', 0)} прогнозов, ошибка {e_m.get('mdape', '—')} %;",
              f"- только по бренду и типу вещи: {e_c.get('n', 0)} прогнозов, ошибка {e_c.get('mdape', '—')} %.", ""]
        L += seg_table(res, lambda tag, key: f"{'Mercari' if tag[0] else 'Goofish'} · {tag[1]} · {tag[2]}", "По бренду и типу вещи")
        L += ["Чем больше ошибка, тем меньше смысла в «−40 % к рынку» для этого сегмента: внутри него вещи слишком разные, "
              "нужна модель или линия в каталоге.", ""]
    else:
        L += ["_Прогнозов нет: таблица snap пуста. Появится после первого пересчёта ленты на новой версии._", ""]

    # ---------------------------------------------------------------- 2. цена сделки на Mercari
    L += ["## 2. Цена сделки на Mercari (проданные лоты)", ""]
    obs = [(ts(seen), ([("m", brand, model)] if model else []) + [("c", brand, cat_)], price, (brand, cat_))
           for seen, brand, model, cat_, price in q("SELECT seen, brand, model, category, price FROM sold WHERE brand != '' AND price > 0")]
    res2 = walk_forward(obs)
    e2 = errors([(p, f) for _, p, f, _, _ in res2])
    if e2["n"] >= MIN_SEG:
        L += [f"Прогнозов: {e2['n']}. Медианная ошибка {e2['mdape']} %, в пределах ±25 % — {e2['in25']} %.", ""]
        L += seg_table(res2, lambda tag, key: f"{tag[0]} · {tag[1]}", "По бренду и типу вещи")
        dd = [d for (d,) in q("SELECT days FROM sold WHERE days IS NOT NULL")]
        if len(dd) >= MIN_SEG:
            L += [f"Срок продажи (от публикации): медиана {statistics.median(dd):.1f} дн., четверть уходит быстрее чем за "
                  f"{statistics.quantiles(dd, n=4)[0]:.1f} дн.", ""]
    else:
        L += [f"_Проданных лотов {n_sold}, прогнозов {e2['n']} — мало для выводов (нужно от {MIN_SEG}). Копятся, когда во вкладке "
              "Mercari листаете выдачу с фильтром «売り切れ»._", ""]

    # ---------------------------------------------------------------- 3. цена продажи в России
    L += ["## 3. Цена продажи в России: каталог против рынка", "",
          "Каталог — то, что Щебень подставляет как «продам за». Рынок — цены постов каналов, Avito, Oskelly и ручной ввод "
          "(это цены, которые просят; настоящая сделка обычно ниже).", ""]
    by_b: dict = defaultdict(list)
    for r in g.market_rows(db, cat, days=3650):
        b = cat["brands"][r["brand"]]
        m = next((m for m in b.get("models", []) if m["name"] == r["model"]), None) if r["model"] else None
        sell = (m or {}).get("sell_rub") or (b.get("sell_rub") or {}).get(r["ckey"]) or 0
        if sell and r["price_rub"]:
            by_b[b["name"]].append((sell, r["price_rub"]))
    rows = sorted(((k, v) for k, v in by_b.items() if len(v) >= 5), key=lambda x: -len(x[1]))
    if rows:
        L += ["| Бренд | Объявлений | Рынок к каталогу (медиана) | Ошибка каталога |", "|---|---|---|---|"]
        for k, v in rows[:20]:
            ratio = statistics.median(f / p for p, f in v)
            L.append(f"| {k} | {len(v)} | {ratio:.2f}× | {errors(v)['mdape']} % |")
        off = [k for k, v in rows if not 0.75 <= statistics.median(f / p for p, f in v) <= 1.35]
        L += ["", "«1.00×» — каталог совпадает с рынком; меньше — каталог завышен, больше — занижен."
              + (f" Сильно расходятся: {', '.join(off)}." if off else ""), ""]
    else:
        L += ["_Объявлений российского рынка с распознанным брендом меньше 5 на бренд._", ""]
    own = [(d.get("sell_rub"), d.get("sold_rub")) for d in deals if d["status"] == "продано" and d.get("sold_rub") and d.get("sell_rub")]
    L += ([f"Ваши продажи: {len(own)}; прогноз продажи расходится с фактом в среднем на {errors(own)['mape']} %.", ""] if own else
          ["_Собственных продаж в «Сделках» нет — сверить прогноз с настоящими деньгами не на чем._", ""])

    # ---------------------------------------------------------------- 4. топ ленты и исходы
    L += ["## 4. Что стало с вещами по группам оценки", "",
          "Берутся прогнозы, записанные в момент появления вещи. Снижение цены продавцом — признак, что по первой цене "
          "вещь не брали; продажа и ★ — признаки, что оценка была верной.", ""]
    drops = {i for (i,) in q("SELECT id FROM items WHERE prev_price > price AND price > 0")}
    sold_ids = {i for (i,) in q("SELECT id FROM sold")}
    marks = {i: (m, r or "") for i, m, r in q(
        "SELECT i.id, i.mark, (SELECT reason FROM marks_log l WHERE l.id = i.id AND l.mark = 'hide' ORDER BY ts DESC LIMIT 1) "
        "FROM items i WHERE i.mark != ''")}
    live = q("SELECT id, total, site FROM snap WHERE late = 0 AND total IS NOT NULL")
    if len(live) >= MIN_SEG:
        L += ["| Оценка | Вещей | Цена снижена | Продано (Mercari) | ★ | Скрыто вами |", "|---|---|---|---|---|---|"]
        for name, lo, hi in (("75 и выше", 75, 999), ("60–74", 60, 75), ("ниже 60", 0, 60)):
            ids = [i for i, t, _ in live if lo <= t < hi]
            if ids:
                pc = lambda s: f"{100 * sum(i in s for i in ids) / len(ids):.1f} %"
                L.append(f"| {name} | {len(ids)} | {pc(drops)} | {pc(sold_ids)} | {pc({i for i, (m, _) in marks.items() if m == 'like'})} "
                         f"| {pc({i for i, (m, _) in marks.items() if m == 'hide'})} |")
        L.append("")
    else:
        L += [f"_Прогнозов, записанных в момент появления, {len(live)} — мало (нужно от {MIN_SEG}). Накопятся за несколько дней работы._", ""]

    # ---------------------------------------------------------------- 5. признаки подделки
    L += ["## 5. Признаки подделки против ваших отметок", ""]
    if quick:
        L += ["_Пропущено (--quick)._", ""]
    else:
        seen = {r["id"]: (r, "") for r in good} | {r["id"]: (r, str(r.get("reject", "")).split(":")[0]) for r in bad}
        fakes = [seen[i] for i, l in label.items() if l == "подделка" and i in seen]
        reals = [seen[i] for i, l in label.items() if l != "подделка" and i in seen]
        flags = [("отсеяна сама (любая причина)", lambda r, why: bool(why)),
                 ("отсеяна как подделка или продавец реплик", lambda r, why: why in ("вероятная подделка", "продавец реплик", "слово-маркер", "чужое фото")),
                 ("риск подделки средний или высокий", lambda r, why: r.get("fake_risk") in ("средний", "высокий")),
                 ("цена ниже половины медианы группы", lambda r, why: isinstance(r.get("price_vs_median"), float) and r["price_vs_median"] < 0.5),
                 ("нет 验货宝", lambda r, why: r.get("site") != "mercari" and "验货宝" not in str(r.get("tags", ""))),
                 ("уровень B (дёшево, проверить)", lambda r, why: r.get("tier") == "B"),
                 ("то же фото или текст у других продавцов", lambda r, why: bool(r.get("sim_photo") or r.get("sim_text") or r.get("same_photo")))]
        if len(fakes) >= 5 and len(reals) >= 5:
            L += [f"Подделок по вашим отметкам и экспертизам: {len(fakes)}; оригиналов и ★: {len(reals)}. Отметки при оценке убраны.", "",
                  "| Признак | У подделок | У ★ и оригиналов |", "|---|---|---|"]
            for name, fn in flags:
                pf, pr = (100 * sum(fn(r, w) for r, w in xs) / len(xs) for xs in (fakes, reals))
                L.append(f"| {name} | {pf:.0f} % | {pr:.0f} % |")
            miss = sum(1 for r, w in fakes if not w and r.get("fake_risk") == "низкий")
            L += ["", f"Подделок, которые Щебень пропустил бы в ленту с низким риском: {miss} из {len(fakes)}. "
                  "Признак полезен, когда у подделок он встречается намного чаще, чем у оригиналов. Выборка маленькая — "
                  "это ориентир, а не вывод.", ""]
        else:
            L += [f"_Размеченных вещей мало: подделок {len(fakes)}, оригиналов и ★ {len(reals)} (нужно хотя бы по 5)._", ""]

    # ---------------------------------------------------------------- 6. чего не хватает
    need = []
    if (n_live or 0) < 2000:
        need.append("прогнозы «в момент появления» — копятся сами, пока Щебень работает")
    if n_sold < 300:
        need.append("проданные лоты Mercari — листать выдачу с фильтром «売り切れ» по нужным брендам (цель — от 300)")
    if n_done < 10:
        need.append("ваши продажи на вкладке «Сделки»: купил → продал, за сколько (цель — от 10)")
    if n_exp < 30:
        need.append("результаты экспертиз на «Сделках» (цель — 30–50)")
    L += ["## 6. Чего не хватает", ""] + ([f"- {x};" for x in need] or ["- данных достаточно для следующего этапа."]) + [""]
    db.close()
    shutil.rmtree(tmp, ignore_errors=True)
    return "\n".join(L)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    text = run(ROOT / "out" / "items.db", quick="--quick" in sys.argv)
    out = ROOT / "reports"
    out.mkdir(exist_ok=True)
    f = out / f"бэктест-{date.today().isoformat()}.md"
    f.write_text(text, encoding="utf-8")
    print(text)
    print(f"\nОтчёт: {f}")
