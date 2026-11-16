"""Проверка «приложение стартует»: поднимает сервер Щебня на КОПИИ базы и на другом порту (8799), открывает
главные страницы и выходит. Работающий Щебень, его база, settings.json, .env и папка profile не затрагиваются:
настройки с секретами подменены пустыми, файлы поисков, расходов и рынка — копиями во временной папке.

    python tests/start_check.py        # код возврата 0 — сервер поднялся и все страницы ответили
"""
import os as _os, tempfile as _tf
_os.environ.setdefault("SHEBEN_LOG_DIR", _tf.mkdtemp(prefix="sheben_logs_"))     # журнал тестов — во временную папку
import http.server
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)      # выход через os._exit не сбрасывает буфер
sys.modules["ai_check"] = None          # без ключей: проверка фото и бот в этой проверке выключены
PORT = 8799
_sleep = time.sleep
time.sleep = lambda x: _sleep(10 ** 6 if x in (300, 60, 3600) else x)     # фоновые потоки (курс, каналы, копии) не просыпаются


class Srv(http.server.ThreadingHTTPServer):
    def __init__(self, addr, h):
        super().__init__((addr[0], PORT), h)


http.server.ThreadingHTTPServer = Srv
import goofish_parser_pro as g  # noqa: E402
import gp_market  # noqa: E402
import gp_pages  # noqa: E402
import gp_score  # noqa: E402

# SHEBEN_START_DIR — своя папка вместо временной: два запуска подряд покажут старт с памятью оценок прошлого запуска
tmp = Path(os.environ.get("SHEBEN_START_DIR") or tempfile.mkdtemp(prefix="sheben_start_"))
tmp.mkdir(parents=True, exist_ok=True)
for name, mod, attr in (("costs.json", gp_score, "COSTS_FILE"), ("searches.json", gp_market, "SEARCHES_FILE"),
                        ("market.json", gp_market, "MARKET_FILE"), ("orders.json", gp_market, "ORDERS_FILE"),
                        ("brand_cn.json", gp_pages, "BRAND_CN_FILE")):
    if (ROOT / name).exists():
        shutil.copy(ROOT / name, tmp / name)
    setattr(mod, attr, tmp / name)
src = sqlite3.connect(f"file:{ROOT / 'out' / 'items.db'}?mode=ro", uri=True)
dst = sqlite3.connect(tmp / "items.db")
src.backup(dst)
dst.close()
src.close()
g.ai_settings = lambda: {}
g.update_rates = lambda force=False: ""
g.save_settings_keys = lambda **kw: None
threading.Thread(target=g.main, args=(["--listen", "--db", str(tmp / "items.db"), "--out", str(tmp)],), daemon=True).start()

H = {"Host": "127.0.0.1:8765", "X-Goofish-UI": "1"}


def _save_cache() -> bool:
    """Ждём фоновое сохранение памяти оценок (через 20 с после готовности ленты)."""
    for _ in range(120):
        if (tmp / "cache_eval.pkl").exists() and (tmp / "cache_ts.pkl").exists():
            return True
        _sleep(0.5)
    return False


def get(path: str, timeout: int = 180) -> bytes:
    return urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", headers=H), timeout=timeout).read()


t0, up = time.time(), False
while time.time() - t0 < 60:        # сервер должен ответить быстро: первый пересчёт ленты идёт в фоне
    try:
        get("/boot", 2)
        up = True
        break
    except Exception:
        _sleep(0.3)
if not up:
    print("СБОЙ: сервер не ответил за минуту")
    os._exit(1)
t_up = time.time() - t0
splash = urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{PORT}/home", headers=dict(H, Accept="text/html")), timeout=10).read()
boot = json.loads(get("/boot", 5))
sp_ok = boot["ready"] or (b"Sheben" in splash and b"/boot" in splash and b"__ETA__" not in splash)
print(f"сервер ответил через {t_up:.1f} с; заставка во время загрузки: {'ок' if sp_ok else 'СБОЙ'} ({len(splash) // 1024} КБ)")
while time.time() - t0 < 300 and not json.loads(get("/boot", 5))["ready"]:
    _sleep(0.5)
if not json.loads(get("/boot", 5))["ready"]:
    print("СБОЙ: лента не посчиталась за 5 минут")
    os._exit(1)
print(f"лента готова через {time.time() - t0:.0f} с")
bad = 0 if sp_ok else 1
for path, check in (("/", lambda d: len(d) > 20000), ("/home", lambda d: b"s_picks" in d and b"o_lead" in d),
                    ("/feed", lambda d: "items" in json.loads(d)), ("/orders", lambda d: "items" in json.loads(d)),
                    ("/searches", lambda d: "items" in json.loads(d)), ("/deals", lambda d: len(d) > 1000),
                    ("/run/summary", lambda d: "Сводка прогона" in json.loads(d)["text"])):
    try:
        d = get(path)
        ok = check(d)
    except Exception as e:
        d, ok = b"", False
        print(f"   {path}: {e!r}"[:200])
    bad += not ok
    print(f"{'ок  ' if ok else 'СБОЙ'} {path}  {len(d) // 1024} КБ")
if os.environ.get("SHEBEN_START_DIR"):      # сохранить память оценок сразу, не дожидаясь фонового сохранения
    print("память оценок сохранена" if _save_cache() else "память оценок не сохранена")
print("старт проверен: все страницы ответили" if not bad else f"СБОЙ: не ответили {bad}")
os._exit(1 if bad else 0)
