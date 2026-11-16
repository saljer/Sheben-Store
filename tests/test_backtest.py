"""Проверка бэктест-стенда: прогноз не видит будущего.  python tests/test_backtest.py"""
import os as _os, tempfile as _tf
_os.environ.setdefault("SHEBEN_LOG_DIR", _tf.mkdtemp(prefix="sheben_logs_"))     # журнал тестов — во временную папку
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")
from gp_backtest import errors, walk_forward

# цены группы растут со временем: прогноз по прошлому всегда ниже факта; увидь он будущее — совпал бы
obs = [(t, [("g",)], 100.0 * t, t) for t in range(1, 11)]
res = walk_forward(obs, min_hist=3)
assert [r[0] for r in res] == [4, 5, 6, 7, 8, 9, 10], "первые три наблюдения — без прогноза (нет истории)"
for tag, pred, fact, key, n in res:
    assert n == tag - 1, "в истории только более ранние наблюдения"
    assert pred < fact, "прогноз построен без самого наблюдения и без будущих"
# наблюдения с одним временем друг друга не видят
res = walk_forward([(1, [("g",)], 10.0, "a"), (1, [("g",)], 10.0, "b"), (1, [("g",)], 10.0, "c"), (1, [("g",)], 999.0, "d")], min_hist=3)
assert res == [], "одновременные наблюдения не служат историей друг другу"
# запасной ключ: по модели истории нет — берётся бренд и тип
obs = [(t, [("m", "редкая")] if t == 9 else [("m", "x"), ("c",)], 50.0, t) for t in range(1, 9)] + [(9, [("m", "редкая"), ("c",)], 60.0, 9)]
res = walk_forward(obs, min_hist=3)
assert res[-1][3] == ("c",) and res[-1][1] == 50.0
assert errors([(100, 100), (150, 100)]) == {"n": 2, "mdape": 25, "mape": 25, "in25": 50}
print("бэктест: утечки будущего нет, тесты пройдены")
