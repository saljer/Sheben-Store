"""Единый конфиг Sheben'Store.

Секреты — в файле .env рядом с программой (в git не попадает, образец — .env.example).
Настраиваемые параметры — значения по умолчанию ниже; чтобы поменять, создайте config.json рядом с программой
с нужными ключами, например {"watch_gap": 20, "jitter": 0.5}. Править код для этого не нужно.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENV_FILE = HERE / ".env"
CONFIG_FILE = HERE / "config.json"

# ключ в настройках приложения -> имя в .env
SECRET_KEYS = {"gemini_api_key": "GEMINI_API_KEY", "tg_bot_token": "TG_BOT_TOKEN", "tg_chat_id": "TG_CHAT_ID",
               "phone_token": "PHONE_TOKEN"}

DEFAULTS = {
    # --- автопоиск Goofish: запросов меньше и реже (этап 3)
    "watch_gap": 14,            # секунд между поисками
    "page_gap": 10,             # секунд до следующей страницы того же поиска
    "mercari_gap": 9,           # секунд после шага Mercari
    "night_slow": 3,            # во сколько раз реже в тихие часы по Пекину
    "jitter": 0.35,             # случайный разброс пауз: 0.35 = от −0 до +35 % к паузе (меньше базовой пауза не бывает)
    "backoff_base_min": 30,     # пауза после первого ограничения сайта (RGV587, проверка), минут
    "backoff_factor": 2.0,      # во сколько раз растёт пауза после каждого следующего ограничения
    "backoff_max_min": 240,     # потолок паузы, минут
    "block_max_per_day": 3,     # после стольких ограничений за сутки автопоиск останавливается до вашей команды
    # --- журнал (этап 4)
    "log_dir": "logs",          # папка журналов рядом с программой
    "log_max_mb": 5,            # размер одного файла журнала, МБ
    "log_backups": 5,           # сколько старых файлов хранить
    "log_level": "INFO",        # INFO / WARNING / ERROR
}

_cfg: dict | None = None


def load_config() -> dict:
    """Значения по умолчанию + config.json. Неизвестные ключи и значения другого типа не берём."""
    global _cfg
    c = dict(DEFAULTS)
    try:
        user = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        user = {}
    for k, v in (user.items() if isinstance(user, dict) else []):
        if k in DEFAULTS and isinstance(v, (int, float, str)) and not isinstance(v, bool):
            d = DEFAULTS[k]
            try:
                c[k] = str(v) if isinstance(d, str) else type(d)(v)
            except (TypeError, ValueError):
                pass
    _cfg = c
    return c


def cfg(name: str):
    return (_cfg or load_config())[name]


# ---------------------------------------------------------------- .env
def read_env(path: Path | None = None) -> dict:
    """Пары ИМЯ=значение из .env; комментарии и пустые строки пропускаются, кавычки вокруг значения снимаются."""
    out = {}
    try:
        lines = (path or ENV_FILE).read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[k.strip()] = v
    return out


def write_env(updates: dict, path: Path | None = None):
    """Меняет или добавляет значения в .env, остальные строки и комментарии не трогает."""
    path = path or ENV_FILE
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = ["# Секреты Sheben'Store. Этот файл не попадает в git. Образец — .env.example"]
    left = {k: str(v) for k, v in updates.items()}
    for i, line in enumerate(lines):
        k = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
        if k in left:
            lines[i] = f"{k}={left.pop(k)}"
    lines += [f"{k}={v}" for k, v in left.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def secrets_overlay(settings: dict, path: Path | None = None) -> dict:
    """Настройки приложения с секретами из .env (и из переменных окружения — они главнее файла)."""
    env = read_env(path)
    out = dict(settings)
    for key, name in SECRET_KEYS.items():
        v = os.environ.get(name) or env.get(name)
        if v:
            out[key] = v
    return out


def save_secrets(values: dict, path: Path | None = None):
    """values — по ключам настроек (gemini_api_key…): пишет их в .env. Пустое значение стирает секрет."""
    upd = {SECRET_KEYS[k]: v for k, v in values.items() if k in SECRET_KEYS}
    if upd:
        write_env(upd, path)


def migrate_secrets(settings_path: Path, env_path: Path | None = None) -> list[str]:
    """Один раз переносит секреты из settings.json в .env и стирает их в settings.json.
    Возвращает имена перенесённых секретов (без значений). Если в .env значение уже есть, оно главнее."""
    try:
        s = json.loads(settings_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    env = read_env(env_path)
    moved, upd = [], {}
    for key, name in SECRET_KEYS.items():
        v = s.get(key)
        if isinstance(v, str) and v.strip():
            if not env.get(name):
                upd[name] = v.strip()
            s[key] = ""
            moved.append(name)
    if moved:
        if upd:
            write_env(upd, env_path)
        settings_path.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
    return moved
