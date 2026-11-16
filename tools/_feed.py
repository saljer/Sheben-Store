"""Общее для скриптов в tools/: лента Щебня, посчитанная на КОПИИ базы. Рабочая база, приложение, settings.json,
.env и папка profile не затрагиваются; журнал пишется во временную папку."""
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("SHEBEN_LOG_DIR", tempfile.mkdtemp(prefix="sheben_logs_"))
os.environ.setdefault("PYTHONHASHSEED", "0")
sys.path.insert(0, str(ROOT))
sys.argv = sys.argv[:1] + [a for a in sys.argv[1:]]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def load_feed():
    """-> (g, cat, good, bad, db): модуль Щебня, каталог, вещи ленты, отсеянные, соединение с копией базы."""
    argv, sys.argv = sys.argv, sys.argv[:1]
    import goofish_parser_pro as g
    sys.argv = argv
    tmp = Path(tempfile.mkdtemp(prefix="sheben_tool_"))
    src = sqlite3.connect(f"file:{ROOT / 'out' / 'items.db'}?mode=ro", uri=True)
    dst = sqlite3.connect(tmp / "items.db")
    src.backup(dst)
    dst.close()
    src.close()
    a = g.build_args(["--listen"])
    a.low_tier, a.brands, a.types = not a.no_low_tier, set(), set()
    g.load_costs()
    a.rate = g.eff_rate()
    cat = g.load_catalog(Path(a.catalog))
    db = g.open_db(tmp / "items.db", tmp, False)
    g.refresh_own_sales(g.load_deals(db))
    g.refresh_market_sell(db, cat)
    good, bad, _ = g.process(g.load_rows(db, "2000-01-01"), a, cat, {}, tmp)
    for r in good:
        r["gender"] = g.gender_of(r)
    return g, cat, good, bad, db
