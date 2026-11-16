"""Журнал Sheben'Store: файлы с ротацией в logs/, уровни INFO / WARNING / ERROR, отдельный журнал ошибок разбора
(id лота и причина) и сводка прогона (круга автопоиска).

    logs/sheben.log         всё подряд, с датой и уровнем
    logs/parse_errors.log   только сбои на отдельных лотах: какой лот, где и почему

Размер файла, число старых файлов, уровень и папка — в config (log_max_mb, log_backups, log_level, log_dir).
Переменная окружения SHEBEN_LOG_DIR переносит журналы в другую папку (тесты пишут во временную).
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import re
import sys
import threading
import time
from pathlib import Path

import config

_ready = False
_lock = threading.Lock()
LOG = logging.getLogger("sheben")
PARSE = logging.getLogger("sheben.parse")
_ERR = re.compile(r"^\s*!!!|ошибка|traceback|сбой|не запустил|exception", re.I)
_WARN = re.compile(r"не удалось|не получилось|ограничен|проверк[уи]|не прочитан|пропускаю|сервер занят|не найден|остановк", re.I)
# причины отсева, которые относятся к проверке подлинности (для сводки прогона)
AUTH_RX = re.compile(r"стоп-слово|красный флаг|слово-маркер|поддел|реплик|без 验货宝|новое.{0,25}дешевле|цена ниже минимума|"
                     r"цена ниже абсолютного|дешевле ¥|чёрн|только упаковка", re.I)


def log_dir() -> Path:
    d = os.environ.get("SHEBEN_LOG_DIR")
    return Path(d) if d else config.HERE / str(config.cfg("log_dir"))


def setup(console: bool | None = None):
    """Подключает файлы журнала. console: писать ли ещё и в консоль (None — только если программа запущена в консоли)."""
    global _ready
    with _lock:
        if _ready:
            return
        d = log_dir()
        d.mkdir(parents=True, exist_ok=True)
        fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
        size, keep = int(float(config.cfg("log_max_mb")) * 1024 * 1024), int(config.cfg("log_backups"))
        main = logging.handlers.RotatingFileHandler(d / "sheben.log", maxBytes=size, backupCount=keep, encoding="utf-8")
        main.setFormatter(fmt)
        LOG.setLevel(getattr(logging, str(config.cfg("log_level")).upper(), logging.INFO))
        LOG.addHandler(main)
        LOG.propagate = False
        perr = logging.handlers.RotatingFileHandler(d / "parse_errors.log", maxBytes=size, backupCount=keep, encoding="utf-8")
        perr.setFormatter(fmt)
        PARSE.addHandler(perr)          # и в общий журнал тоже (propagate)
        if console is None:
            console = bool(os.environ.get("SHEBEN_LOG_CONSOLE")) or bool(getattr(sys.__stdout__, "isatty", lambda: False)())
        if console and sys.__stdout__ is not None:
            ch = logging.StreamHandler(sys.__stdout__)
            ch.setFormatter(logging.Formatter("%(message)s"))
            LOG.addHandler(ch)
        _ready = True


def level_of(msg: str) -> int:
    return logging.ERROR if _ERR.search(msg) else logging.WARNING if _WARN.search(msg) else logging.INFO


def say(*args, sep: str = " ", end: str = "\n", file=None, flush: bool = False, level: int | None = None):
    """Замена print: та же запись вызова, но строка идёт в журнал с датой и уровнем (уровень — по словам в тексте)."""
    if not _ready:
        setup()
    msg = sep.join(str(a) for a in args).strip()
    if msg:
        LOG.log(level or level_of(msg), msg.replace("\n", " | "))


def lot_id(entry) -> str:
    """id лота из сырой записи ответа сайта — чтобы назвать его в журнале, даже если разбор упал."""
    m = re.search(r"""['"](?:itemId|item_id|id)['"]\s*:\s*['"]?([A-Za-z0-9_]{4,40})""", repr(entry)[:4000])
    return m.group(1) if m else "?"


def parse_error(lot: str, reason: str, where: str = ""):
    """Сбой на одном лоте: в отдельный журнал и в счётчик прогона. Прогон при этом продолжается."""
    if not _ready:
        setup()
    RUN["errors"] += 1
    PARSE.warning("лот %s%s: %s", lot or "?", f" ({where})" if where else "", str(reason)[:300])


def safe_parse(fn, entry, where: str = ""):
    """fn(entry) с защитой: сломанная запись не роняет разбор остальных — она пропускается и попадает в журнал."""
    try:
        return fn(entry)
    except Exception as e:
        parse_error(lot_id(entry), repr(e), where or getattr(fn, "__name__", ""))
        return None


# ---------------------------------------------------------------- сводка прогона (круг автопоиска)
RUN = {"t0": time.time(), "seen": 0, "new": 0, "errors": 0, "auth_pre": 0, "new_ids": set()}


def run_add(seen: int, new_ids=()):
    RUN["seen"] += seen
    RUN["new"] += len(new_ids)
    if len(RUN["new_ids"]) < 20000:
        RUN["new_ids"].update(new_ids)


def run_auth(why: str):
    """Входной фильтр отсеял новый лот: считаем, если причина — про подлинность."""
    if AUTH_RX.search(why or ""):
        RUN["auth_pre"] += 1


def run_summary(rejects: dict | None = None, reset: bool = True) -> str:
    """Сводка с прошлого сброса: просмотрено, новых, ошибок, отсеяно проверкой подлинности. Пишется в журнал."""
    auth = RUN["auth_pre"] + sum(1 for i in RUN["new_ids"] if AUTH_RX.search(str((rejects or {}).get(i, ""))))
    mins = (time.time() - RUN["t0"]) / 60
    text = (f"Сводка прогона ({mins:.0f} мин): лотов просмотрено {RUN['seen']}, новых {RUN['new']}, "
            f"ошибок разбора {RUN['errors']}, отсеяно проверкой подлинности {auth}")
    say(text, level=logging.WARNING if RUN["errors"] else logging.INFO)
    if reset:
        RUN.update(t0=time.time(), seen=0, new=0, errors=0, auth_pre=0, new_ids=set())
    return text


# ---------------------------------------------------------------- stdout / stderr окна -> журнал
class StreamToLog:
    """Для приложения без консоли: всё, что печатают библиотеки и необработанные исключения потоков, — в журнал."""

    def __init__(self, level: int | None):
        self.level, self.buf = level, ""

    def write(self, s):
        self.buf += str(s)
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            if line.strip():
                LOG.log(self.level or level_of(line), line.rstrip())
        return len(str(s))

    def flush(self):
        pass

    def isatty(self):
        return False


def redirect_std():
    setup(console=False)
    sys.stdout, sys.stderr = StreamToLog(None), StreamToLog(logging.ERROR)
