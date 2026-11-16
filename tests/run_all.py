"""Все проверки Щебня одной командой: тесты, регрессия оценок, старт сервера на копии базы.

  python tests/run_all.py            # всё (2–4 минуты)
  python tests/run_all.py --quick    # без старта сервера

Код возврата 0 — всё чисто. Рабочее приложение и его данные не затрагиваются.
"""
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
env = dict(os.environ, PYTHONHASHSEED="0", PYTHONIOENCODING="utf-8")
steps = [("конфиг и секреты", "test_config.py", r"тесты пройдены"), ("бережный режим", "test_throttle.py", r"тесты пройдены"),
         ("журнал", "test_log.py", r"тесты пройдены"), ("бэктест", "test_backtest.py", r"тесты пройдены"), ("память оценок", "test_cache.py", r"тесты пройдены"),
         ("регрессия оценок", "regress.py", r"изменилось 0 из")]
if "--quick" not in sys.argv:
    steps.append(("старт сервера", "start_check.py", r"старт проверен"))
bad = 0
for name, script, want in steps:
    t0 = time.time()
    try:
        r = subprocess.run([sys.executable, str(HERE / script)], capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=env, cwd=HERE.parent, timeout=600)
        out = r.stdout + r.stderr
        ok = r.returncode == 0 and bool(re.search(want, out))
    except subprocess.TimeoutExpired:
        out, ok = "не уложился в 10 минут", False
    bad += not ok
    tail = "" if ok else " | " + " / ".join(x.strip() for x in out.strip().splitlines()[-4:])[:400]
    extra = (re.search(r"изменилось \d+ из \d+[^\n]*", out) or [""])[0] if script == "regress.py" else ""
    print(f"{'ок  ' if ok else 'СБОЙ'} {name} ({time.time() - t0:.0f} с){' — ' + extra if extra else ''}{tail}")
print("ИТОГ: чисто" if not bad else f"ИТОГ: сбоев {bad}")
sys.exit(1 if bad else 0)
