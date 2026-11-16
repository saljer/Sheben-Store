"""Контрольный прогон оценки на замороженной копии базы: что сдвинулось после правки парсера.

  python tests/regress.py --freeze   снять копию out/items.db (tests/base.db) и запомнить оценки как эталон
  python tests/regress.py            пересчитать и сравнить с эталоном; проверить ваши «подделка» и ★
  python tests/regress.py --save     принять текущие оценки как новый эталон

Ваши отметки при расчёте убираются: проверяется, как парсер сам видит эти вещи."""
import os as _os, tempfile as _tf
_os.environ.setdefault("SHEBEN_LOG_DIR", _tf.mkdtemp(prefix="sheben_logs_"))     # журнал тестов — во временную папку
import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
BASE, SNAP = HERE / "base.db", HERE / "snapshot.json"
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")


def freeze():
    src = sqlite3.connect(f"file:{ROOT / 'out' / 'items.db'}?mode=ro", uri=True)
    BASE.unlink(missing_ok=True)
    dst = sqlite3.connect(BASE)
    src.backup(dst)     # целая копия, даже если приложение сейчас пишет в базу
    dst.close()
    src.close()
    print(f"Копия базы: {BASE} ({BASE.stat().st_size // 1024} КБ)")


def run() -> dict:
    import goofish_parser_pro as g
    tmp = Path(tempfile.mkdtemp(prefix="gp_regress_"))
    shutil.copy(BASE, tmp / "items.db")
    a = g.build_args(["--listen"])
    a.low_tier, a.brands, a.types = not a.no_low_tier, set(), set()
    a.rate = 12.9       # фиксированный курс: эталон не должен плыть вместе с курсом
    cat = g.load_catalog(Path(a.catalog))
    db = g.open_db(tmp / "items.db", tmp, False)
    rows = g.load_rows(db, "2000-01-01")
    marks = {r["id"]: (r.get("mark", ""), r.get("mark_reason", "")) for r in rows}
    for r in rows:
        r.update(mark="", mark_reason="")
    t0 = time.perf_counter()
    good, bad, reasons = g.process(rows, a, cat, {}, tmp)
    cold = time.perf_counter() - t0
    t0 = time.perf_counter()
    g.process([dict(r) for r in rows], a, cat, {}, tmp)
    warm = time.perf_counter() - t0
    html = g.render_html(good, bad, reasons, a.rate)
    db.close()
    shutil.rmtree(tmp, ignore_errors=True)
    snap = {r["id"]: [r.get("tier", ""), r.get("total", 0), ""] for r in good}
    snap.update({r["id"]: ["-", 0, str(r.get("reject", ""))[:60]] for r in bad})
    by = {r["id"]: r for r in good}
    fakes = [i for i, (m, why) in marks.items() if m == "hide" and why == "подделка"]
    likes = [i for i, (m, _) in marks.items() if m == "like"]
    fake_fail = [i for i in fakes if i in by and by[i].get("tier") == "A" and by[i].get("fake_risk") == "низкий"]
    like_fail = [i for i in likes if i not in by]
    return {"snap": snap, "good": len(good), "bad": len(bad), "cold": cold, "warm": warm,
            "html": hashlib.md5(html.encode("utf-8")).hexdigest(), "fakes": len(fakes), "likes": len(likes),
            "fake_fail": fake_fail, "like_fail": like_fail,
            "titles": {i: (by[i] if i in by else next(r for r in bad if r["id"] == i)).get("title", "")[:40]
                       for i in fake_fail + like_fail}}


def report(res: dict, ref: dict | None):
    print(f"Прошло в ленту: {res['good']}, отсеяно: {res['bad']}. Пересчёт: {res['cold']:.2f} с, повторный {res['warm']:.2f} с")
    print(f"Ваши «подделка»: {res['fakes']}, из них парсер пропустил бы в A с низким риском: {len(res['fake_fail'])}")
    for i in res["fake_fail"]:
        print(f"   {i}  {res['titles'][i]}")
    print(f"Ваши ★: {res['likes']}, из них не прошли бы в ленту: {len(res['like_fail'])}")
    for i in res["like_fail"]:
        print(f"   {i}  {res['titles'][i]}  ({res['snap'][i][2]})")
    if not ref:
        return
    old, new = ref["snap"], res["snap"]
    common = set(old) & set(new)
    moved = [(i, old[i], new[i]) for i in common if old[i][:2] != new[i][:2]]
    tier = [m for m in moved if m[1][0] != m[2][0]]
    print(f"\nПо сравнению с эталоном: изменилось {len(moved)} из {len(common)}, уровень (A/B/отсеяно) — у {len(tier)}")
    gained = sum(1 for _, o, n in tier if o[0] == "-")
    lost = sum(1 for _, o, n in tier if n[0] == "-")
    print(f"   стали проходить: {gained}, стали отсеиваться: {lost}, A↔B: {len(tier) - gained - lost}")
    why = {}
    for _, o, n in tier:
        if n[0] == "-":
            k = n[2].split(":")[0].split(" (")[0]
            why[k] = why.get(k, 0) + 1
    for k, v in sorted(why.items(), key=lambda kv: -kv[1])[:8]:
        print(f"      отсеяно теперь: {k} — {v}")
    big = sorted((m for m in moved if m[1][0] != "-" and m[2][0] != "-"), key=lambda m: -abs(m[2][1] - m[1][1]))[:8]
    for i, o, n in big:
        print(f"   {i}: {o[0]} {o[1]} → {n[0]} {n[1]}")
    print("   страница ленты", "совпадает байт в байт" if ref.get("html") == res["html"] else "изменилась")


def main():
    if "--freeze" in sys.argv:
        freeze()
    if not BASE.exists():
        sys.exit("Нет tests/base.db: сначала  python tests/regress.py --freeze")
    res = run()
    ref = json.loads(SNAP.read_text(encoding="utf-8")) if SNAP.exists() else None
    report(res, None if "--freeze" in sys.argv else ref)
    if "--freeze" in sys.argv or "--save" in sys.argv or ref is None:
        SNAP.write_text(json.dumps({k: res[k] for k in ("snap", "html")}, ensure_ascii=False), encoding="utf-8")
        print("Эталон сохранён:", SNAP)


if __name__ == "__main__":
    main()
