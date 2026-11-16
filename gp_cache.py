"""Память оценок между запусками. Холодный пересчёт ленты (все вещи оцениваются заново, описания сверяются на шаблоны
реплик) занимает десятки секунд, повторный — пару секунд: разница в трёх словарях в памяти. Здесь они сохраняются на диск
и поднимаются при запуске — но только если с тех пор не менялось то, от чего зависит результат:

  оценки (_EVAL_CACHE)        код оценки, каталог, расходы и курс, цены рынка России, ваши продажи, параметры запуска
  дешёвый люкс (_LUX_CACHE)   код оценки и каталог
  шаблоны текста (_TS)        только код сверки описаний

Не совпала подпись — словарь не берётся, и эта часть считается заново, как раньше. Файлы лежат в out/ (в git не попадают).
"""
from __future__ import annotations

import hashlib
import json
import pickle
import time
from pathlib import Path

import gp_process
import gp_score

HERE = Path(__file__).resolve().parent
MAX_EVAL, MAX_DOCS = 150_000, 200_000       # разросшуюся память не сохраняем: следующий запуск посчитает заново и начнёт с чистой


def _md5(*parts) -> str:
    h = hashlib.md5()
    for p in parts:
        h.update(p if isinstance(p, bytes) else str(p).encode("utf-8"))
    return h.hexdigest()


def _code(*names) -> str:
    return _md5(*[(HERE / n).read_bytes() for n in names])


def signatures(a, cat: dict, prices: dict) -> dict:
    cat_sig = _md5(json.dumps(cat, sort_keys=True, ensure_ascii=False, default=str))
    code = _code("gp_parse.py", "gp_score.py", "gp_process.py", "gp_market.py")
    args = {k: v for k, v in sorted(vars(a).items()) if k not in ("db", "out", "profile", "catalog")}
    return {"ts": _code("gp_process.py"),
            "lux": _md5(code, cat_sig),
            "eval": _md5(code, cat_sig, json.dumps(gp_score.COSTS, sort_keys=True, default=str), repr(args),
                         repr(sorted(gp_score.MARKET_SELL.items())), repr(sorted(gp_score.OWN_SALES.items())),
                         json.dumps(prices, sort_keys=True, ensure_ascii=False, default=str))}


def _parts():
    return {"eval": gp_score._EVAL_CACHE, "lux": gp_process._LUX_CACHE, "ts": gp_process._TS}


def dump(a, cat: dict, prices: dict) -> dict:
    """Снимок памяти в байтах: {имя: bytes}. Вызывать под общим замком (словари в это время не должны меняться)."""
    sig, out = signatures(a, cat, prices), {}
    for name, obj in _parts().items():
        size = len(obj["docs"]) if name == "ts" else len(obj)
        if size and size <= (MAX_DOCS if name == "ts" else MAX_EVAL):
            out[name] = pickle.dumps({"sig": sig[name], "data": obj}, protocol=5)
    return out


def write(outdir: Path, blobs: dict):
    """Запись снимка на диск — можно вне замка. Сначала во временный файл, чтобы обрыв не оставил битый."""
    for name, b in blobs.items():
        tmp = Path(outdir) / f"cache_{name}.tmp"
        tmp.write_bytes(b)
        tmp.replace(Path(outdir) / f"cache_{name}.pkl")


def load(outdir: Path, a, cat: dict, prices: dict) -> dict:
    """Поднимает с диска то, что ещё годится. -> {имя: сколько записей взято}; 0 — подпись не совпала или файла нет."""
    sig, got = signatures(a, cat, prices), {}
    for name, obj in _parts().items():
        got[name] = 0
        try:
            d = pickle.loads((Path(outdir) / f"cache_{name}.pkl").read_bytes())
        except Exception:
            continue
        if d.get("sig") != sig[name] or not isinstance(d.get("data"), dict):
            continue
        obj.clear()
        obj.update(d["data"])
        got[name] = len(obj["docs"]) if name == "ts" else len(obj)
    return got


def save(outdir: Path, a, cat: dict, prices: dict, lock=None) -> float:
    """Снимок под замком, запись вне его. Возвращает, сколько секунд держали замок."""
    t = time.time()
    if lock is not None:
        with lock:
            blobs = dump(a, cat, prices)
    else:
        blobs = dump(a, cat, prices)
    held = time.time() - t
    write(outdir, blobs)
    return held
