"""Память оценок между запусками: сохранённое поднимается и даёт те же оценки; после смены расходов или каталога
оценки с диска не берутся (считаются заново), а сверка описаний — берётся."""
import os as _os, tempfile as _tf
_os.environ.setdefault("SHEBEN_LOG_DIR", _tf.mkdtemp(prefix="sheben_logs_"))
import shutil
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")
sys.argv = sys.argv[:1]
import goofish_parser_pro as g  # noqa: E402
import gp_cache  # noqa: E402
import gp_process  # noqa: E402
import gp_score  # noqa: E402

tmp = Path(_tf.mkdtemp(prefix="sheben_cache_"))
src = sqlite3.connect(f"file:{ROOT / 'out' / 'items.db'}?mode=ro", uri=True)
dst = sqlite3.connect(tmp / "items.db")
src.backup(dst)
dst.close()
src.close()
a = g.build_args(["--listen"])
a.low_tier, a.brands, a.types = not a.no_low_tier, set(), set()
a.rate = 12.9
cat = g.load_catalog(Path(a.catalog))
db = g.open_db(tmp / "items.db", tmp, False)
rows = g.load_rows(db, "2000-01-01")[:6000]          # часть базы — тест идёт секунды


def run():
    t = time.time()
    good, bad, _ = g.process([dict(r) for r in rows], a, cat, {}, tmp)
    snap = {r["id"]: (r.get("tier"), r.get("total"), r.get("fake_risk"), r.get("sell_rub")) for r in good}
    snap.update({r["id"]: ("-", r.get("reject")) for r in bad})
    return snap, time.time() - t


def wipe():
    gp_score._EVAL_CACHE.clear()
    gp_process._LUX_CACHE.clear()
    gp_process._TS.update(docs={}, post={}, out={})


wipe()
ref, cold = run()
held = gp_cache.save(tmp, a, cat, {})
assert all((tmp / f"cache_{n}.pkl").exists() for n in ("eval", "lux", "ts"))
wipe()
got = gp_cache.load(tmp, a, cat, {})
assert got["eval"] >= len(rows) * 0.9 and got["lux"] and got["ts"], got
again, warm = run()
assert again == ref, "оценки после чтения памяти с диска отличаются"
assert warm < cold * 0.6, (cold, warm)
# расходы изменились — оценки с диска не годятся, а сверка описаний от расходов не зависит
wipe()
old = gp_score.COSTS["ru_ship"]
gp_score.COSTS["ru_ship"] = old + 100
got = gp_cache.load(tmp, a, cat, {})
gp_score.COSTS["ru_ship"] = old
assert got["eval"] == 0 and got["lux"] > 0 and got["ts"] > 0, got
# каталог изменился — не годятся ни оценки, ни «дешёвый люкс»
wipe()
cat2 = dict(cat, default_sell=dict(cat["default_sell"], clothing=cat["default_sell"]["clothing"] + 1))
got = gp_cache.load(tmp, a, cat2, {})
assert got["eval"] == 0 and got["lux"] == 0 and got["ts"] > 0, got
# битый файл не роняет запуск
(tmp / "cache_eval.pkl").write_bytes(b"not a pickle")
assert gp_cache.load(tmp, a, cat, {})["eval"] == 0
db.close()
shutil.rmtree(tmp, ignore_errors=True)
print(f"память оценок: тесты пройдены (холодный пересчёт {cold:.1f} с, с памятью {warm:.1f} с, замок на запись {held:.2f} с)")
