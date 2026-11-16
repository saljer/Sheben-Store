"""Бережный режим автопоиска: разброс пауз и нарастающая пауза после ограничения Goofish."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import gp_throttle as t  # noqa: E402

# разброс: пауза не короче базовой и не длиннее базовой × (1 + jitter); значения разные
vals = [t.jittered(14, 0.35) for _ in range(500)]
assert min(vals) >= 14 and max(vals) <= 14 * 1.35 + 1e-9 and len({round(v, 3) for v in vals}) > 100
assert t.jittered(10, 0, lambda: 0.9) == 10 and t.jittered(10, 0.5, lambda: 1.0) == 15 and t.jittered(10, -1) == 10
# нарастающая пауза: 30, 60, 120, 240, дальше потолок
assert [t.backoff_minutes(n, 30, 2, 240) for n in (1, 2, 3, 4, 5, 9)] == [30, 60, 120, 240, 240, 240]
# журнал ограничений: сутки, один случай за 10 минут не считается дважды, переживает перезапуск
p = Path(tempfile.mkdtemp(prefix="gp_thr_")) / "blocks.json"
b = t.BlockLog(p)
now = 1_000_000.0
a = b.plan(now, 30, 2, 240, 3)
assert a["n"] == 1 and a["wait_min"] == 30 and not a["halt"] and a["resume_at"] == now + 1800
assert b.plan(now + 60, 30, 2, 240, 3)["n"] == 1                 # тот же случай: сайт и адрес страницы сообщили оба
c = b.plan(now + 3600, 30, 2, 240, 3)
assert c["n"] == 2 and c["wait_min"] == 60 and not c["halt"]
d = t.BlockLog(p).plan(now + 3 * 3600, 30, 2, 240, 3)             # после «перезапуска» счёт не обнулился
assert d["n"] == 3 and d["wait_min"] == 120 and d["halt"]          # третье за сутки — стоп до команды
e = t.BlockLog(p).plan(now + 3 * 3600 + 90000, 30, 2, 240, 3)     # через сутки с лишним счёт начинается заново
assert e["n"] == 1 and not e["halt"]
print("бережный режим: тесты пройдены")
