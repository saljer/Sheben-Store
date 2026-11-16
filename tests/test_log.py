"""Журнал и отказоустойчивость: сломанный лот не роняет разбор остальных и попадает в отдельный журнал с id и причиной;
сводка прогона считает просмотренные, новые, ошибки и отсев по подлинности; файлы журнала ротируются."""
import os
import sys
import tempfile
from pathlib import Path

tmp = Path(tempfile.mkdtemp(prefix="sheben_logs_"))
os.environ["SHEBEN_LOG_DIR"] = str(tmp)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import gp_log  # noqa: E402
import gp_parse  # noqa: E402

gp_log.setup(console=False)


def boom(entry):
    if entry.get("bad"):
        raise KeyError("price")
    return {"id": entry["itemId"]}


entries = [{"itemId": "1001"}, {"itemId": "1002", "bad": True}, {"itemId": "1003"}]
rows = [r for r in (gp_log.safe_parse(boom, e, "тест") for e in entries) if r]
assert [r["id"] for r in rows] == ["1001", "1003"], rows                      # прогон не остановился
perr = (tmp / "parse_errors.log").read_text(encoding="utf-8")
assert "лот 1002 (тест)" in perr and "KeyError" in perr and "WARNING" in perr, perr
assert "лот 1002" in (tmp / "sheben.log").read_text(encoding="utf-8")        # и в общем журнале тоже
# настоящий разборщик Mercari: мусор вместо записи пропускается, остальное разбирается
kind, got, _, _ = gp_parse.parse_mercari("https://api.mercari.jp/v2/entities:search", {"items": [None, 5, "x"]})
assert got == [] and gp_log.RUN["errors"] >= 1
# уровни по тексту строки
assert gp_log.level_of("!!! ОСТАНОВКА") == 40 and gp_log.level_of("курс не удалось получить") == 30 and gp_log.level_of("«prada»: 30 объявлений") == 20
gp_log.say("обычная строка", "из двух частей")
gp_log.say("ошибка обработки: ValueError")
main = (tmp / "sheben.log").read_text(encoding="utf-8")
assert "INFO    обычная строка из двух частей" in main and "ERROR   ошибка обработки" in main
# сводка прогона
gp_log.RUN.update(seen=0, new=0, errors=2, auth_pre=0, new_ids=set())
gp_log.run_add(30, {"a", "b", "c"})
gp_log.run_add(30, {"d"})
gp_log.run_auth("стоп-слово: 复刻")
gp_log.run_auth("только упаковка: 鞋盒")
gp_log.run_auth("цена выше максимума")                                        # не про подлинность
text = gp_log.run_summary({"a": "красный флаг: 高仿", "b": "убыточно по расчёту", "zzz": "подделка"})
assert "просмотрено 60" in text and "новых 4" in text and "ошибок разбора 2" in text and "подлинности 3" in text, text
assert gp_log.RUN["seen"] == 0 and gp_log.RUN["errors"] == 0                  # после сводки счётчики сброшены
# ротация: файл не растёт без предела
h = next(x for x in gp_log.LOG.handlers if getattr(x, "baseFilename", "").endswith("sheben.log"))
h.maxBytes = 2000
for i in range(200):
    gp_log.say(f"строка {i} " + "x" * 50)
assert (tmp / "sheben.log.1").exists() and (tmp / "sheben.log").stat().st_size <= 2200
# stdout окна -> журнал
w = gp_log.StreamToLog(None)
w.write("первая\nвторая без конца")
assert "первая" in (tmp / "sheben.log").read_text(encoding="utf-8") + (tmp / "sheben.log.1").read_text(encoding="utf-8")
print("журнал и отказоустойчивость: тесты пройдены")
