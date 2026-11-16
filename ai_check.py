"""Проверка фото вещи через Gemini: бренд и тип вещи по фото (когда в тексте их нет), мужское/женское,
состояние и риск подделки. Результат — подсказка для оценки, не экспертиза.

Ключ и настройки — в settings.json рядом со скриптом (ключ вписываете вы, программа его никуда не пишет)."""
import json
import re
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
SETTINGS = HERE / "settings.json"
DEFAULTS = {
    "_help": "Секреты (ключ Gemini, токен бота) хранятся в файле .env рядом с программой, образец — .env.example. "
             "ai_daily_limit: сколько проверок в день максимум. ai_min_price: проверять вещи от этой цены, ¥. "
             "gemini_model: модель (если такой нет, программа сама выберет доступную flash-модель). "
             "tg_bot_token: токен вашего бота от @BotFather (уведомления в Telegram); напишите боту /start — "
             "tg_chat_id заполнится сам. tg_min_profit: присылать новые вещи только с прибылью от N ₽.",
    "gemini_api_key": "",
    "tg_bot_token": "",
    "tg_chat_id": "",
    "tg_min_profit": 0,
    "gemini_model": "gemini-flash-latest",
    "ai_daily_limit": 150,
    "ai_min_price": 500,
}
# запасные модели: у бесплатного уровня дневной лимит считается на каждую модель отдельно
FALLBACK_MODELS = ["gemini-flash-latest", "gemini-flash-lite-latest", "gemini-3.5-flash-lite", "gemini-3.8-flash"]
MANUAL_RESERVE = 20      # столько проверок в день фон оставляет для кнопок «ИИ» и «Gemini»
BG_GAP = 20              # секунд между фоновыми проверками — не упираться в лимит запросов в минуту
CATEGORIES = ["bag", "shoes", "sneakers", "outerwear", "clothing", "jewelry", "accessories", "other"]
SCHEMA = {
    "type": "object",
    "properties": {
        "brand": {"type": "string", "description": "Бренд, если виден по логотипу/принту/бирке, иначе пустая строка"},
        "category": {"type": "string", "enum": CATEGORIES},
        "item": {"type": "string", "description": "Что за вещь, 2–5 слов по-русски"},
        "gender": {"type": "string", "enum": ["men", "women", "unisex", "unknown"]},
        "condition_pct": {"type": "integer", "description": "Состояние по фото, 0–100"},
        "studio_photo": {"type": "boolean", "description": "Студийный свет/белый фон/фото как из магазина"},
        "packaging_shown": {"type": "boolean", "description": "В кадре коробки, пакеты, пыльники"},
        "fake_risk": {"type": "string", "enum": ["low", "medium", "high"]},
        "fake_signs": {"type": "array", "items": {"type": "string"}, "description": "Конкретные признаки подделки, по-русски"},
        "verdict": {"type": "string", "description": "Одно предложение по-русски: стоит ли брать и почему"},
    },
    "required": ["brand", "category", "item", "gender", "condition_pct", "studio_photo", "packaging_shown",
                 "fake_risk", "fake_signs", "verdict"],
}
PROMPT = """Ты помогаешь перекупщику мужского люкса и архивной одежды проверять объявления с Goofish (Xianyu).
Посмотри на фото и текст объявления и ответь строго по схеме.
- brand: определи бренд по логотипу, принту, бирке, фурнитуре или узнаваемой модели (например, принт Chateau Marmont с флейтистом — Gucci SS19).
  Продавцы часто указывают бренд неверно — верь фото, а не тексту. Если не уверен — пустая строка.
- category: тип вещи. gender: для кого вещь (women — явно женская модель/крой).
- fake_risk: оцени риск подделки по тому, что видно: шрифт и пропорции логотипа, качество швов и материалов, фурнитура,
  витринная съёмка новой вещи с коробками по низкой цене, фото как из каталога. Это не экспертиза — оцени риск честно.
- fake_signs: только конкретные увиденные признаки. verdict: коротко, по-русски.

Объявление: {title}
Цена: ¥{price}
Описание: {desc}"""

_lock = threading.Lock()


def load_settings() -> dict:
    if not SETTINGS.exists():
        SETTINGS.write_text(json.dumps(DEFAULTS, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        s = json.loads(SETTINGS.read_text(encoding="utf-8"))
    except Exception:
        s = {}
    import config       # секреты (ключ Gemini, токен бота, ключ телефона) — из .env, они главнее settings.json
    return config.secrets_overlay({**DEFAULTS, **s})


def init_db(db):
    db.execute("CREATE TABLE IF NOT EXISTS ai_checks(id TEXT PRIMARY KEY, ts TEXT, model TEXT, result TEXT, error TEXT)")
    db.commit()


def results(db) -> dict:
    out = {}
    for iid, res in db.execute("SELECT id, result FROM ai_checks WHERE result IS NOT NULL AND result != ''"):
        try:
            out[iid] = json.loads(res)
        except Exception:
            pass
    return out


def used_today(db) -> int:
    day = datetime.now().strftime("%Y-%m-%d")
    return db.execute("SELECT count(*) FROM ai_checks WHERE ts LIKE ?", (day + "%",)).fetchone()[0]


def _image(url: str) -> bytes:
    if url.startswith("//"):
        url = "https:" + url
    url = url.replace("http://", "https://")
    if "alicdn.com" in url and "_400x400" not in url:
        url += "_400x400.jpg"      # уменьшенная копия: быстрее и дешевле, деталей хватает
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read()


class Checker:
    """Фоновая проверка: очередь вещей, дневной лимит, пауза при ограничении сервиса."""

    def __init__(self, db, db_lock, on_result, log=print):
        self.db, self.db_lock, self.on_result, self.log = db, db_lock, on_result, log
        self.queue: list[dict] = []          # приоритетные (кнопка) в начале
        self.status = {"enabled": False, "error": "", "model": "", "pause_until": 0.0}
        self.client = None
        with db_lock:
            init_db(db)
        threading.Thread(target=self._loop, daemon=True).start()

    def _client(self, s):
        key = (s.get("gemini_api_key") or "").strip()
        if not key:
            self.status.update(enabled=False, error="нет ключа: впишите GEMINI_API_KEY в файл .env")
            return None
        if self.client is None or self.status.get("key_tail") != key[-4:]:
            from google import genai
            from google.genai import types
            self.client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=60000))   # 60 с, не виснуть
            self.status.update(key_tail=key[-4:], model=s.get("gemini_model") or DEFAULTS["gemini_model"])
        self.status.update(enabled=True)
        return self.client

    def set_queue(self, rows: list[dict]):
        with _lock:
            pri = [r for r in self.queue if r.get("_pri")]
            ids = {r["id"] for r in pri}
            self.queue = pri + [r for r in rows if r["id"] not in ids]

    def push_front(self, row: dict):
        with _lock:
            self.queue = [dict(row, _pri=True)] + [r for r in self.queue if r["id"] != row["id"]]

    def _pick_model(self, client) -> str:
        names = []
        for m in client.models.list():
            acts = getattr(m, "supported_actions", None) or []
            if "flash" in (m.name or "") and (not acts or "generateContent" in acts):
                names.append(m.name.split("/")[-1])
        names.sort(key=lambda n: ("lite" in n, "preview" in n or "exp" in n, n), reverse=False)
        return names[0] if names else ""

    def check(self, row: dict) -> dict:
        from google.genai import errors, types
        s = load_settings()
        client = self._client(s)
        if client is None:
            raise RuntimeError(self.status["error"])
        img = _image(row["image"])
        prompt = PROMPT.format(title=row.get("title", "")[:200], price=f'{row.get("price_cny", 0):.0f}',
                               desc=(row.get("desc", "") + " " + row.get("labels", ""))[:700])
        cfg = types.GenerateContentConfig(response_mime_type="application/json", response_json_schema=SCHEMA)
        parts = [types.Part.from_bytes(data=img, mime_type="image/jpeg"), prompt]
        return json.loads(self._gen(client, parts, cfg).text)

    def _gen(self, client, parts, cfg=None):
        """Запрос с запасными моделями: при перегрузке (503/504) или отсутствии модели (404) — следующая."""
        from google.genai import errors
        today = datetime.now().strftime("%Y-%m-%d")
        dead = self.status.setdefault("dead", {})      # модель → день, когда у неё кончился лимит / её убрали
        models = [m for m in [self.status["model"]] + FALLBACK_MODELS if dead.get(m) != today]
        models = list(dict.fromkeys(models))
        last = None
        for m in models:
            try:
                resp = client.models.generate_content(model=m, contents=parts, config=cfg)
                if m != self.status["model"]:
                    self.log(f"ИИ: {self.status['model']} недоступна/лимит, работаю на {m}")
                    self.status["model"] = m
                return resp
            except (errors.ServerError, errors.ClientError) as e:
                code = getattr(e, "code", 0)
                if code == 429 and "PerDay" in str(e):     # дневной лимит этой модели — до завтра берём другую
                    dead[m] = today
                elif code == 404:
                    dead[m] = today
                elif code not in (500, 503, 504):
                    raise
                last = e
        raise last or RuntimeError("все модели Gemini на сегодня исчерпали лимит")

    def brand_cn(self, brand: str) -> str:
        """Как китайские продавцы на 闲鱼 пишут бренд: иероглифы или "" (пишут латиницей). Один короткий запрос."""
        s = load_settings()
        client = self._client(s)
        if client is None:
            raise RuntimeError(self.status["error"])
        prompt = (f"Brand: {brand}. How do Chinese sellers on Xianyu (闲鱼) usually write this fashion brand in listing titles? "
                  "If there is a common Chinese name, answer with that Chinese name only (Chinese characters, no latin, "
                  "no explanation). If sellers usually write it in Latin letters, answer NONE.")
        t = self._gen(client, [prompt]).text.strip().splitlines()[0].strip(" .。\"'“”")
        return t if re.fullmatch(r"[一-鿿·]{2,10}", t) else ""

    def photo_query(self, row: dict, hint: str = "") -> str:
        """Кнопка «Похожее»: Gemini смотрит фото и пишет поисковый запрос для 闲鱼 — бренд так, как пишут продавцы,
        модель или линия, тип, цвет, материал. Одна строка, не длиннее 60 знаков."""
        s = load_settings()
        client = self._client(s)
        if client is None:
            raise RuntimeError(self.status["error"])
        from google.genai import types
        img = _image(row["image"])
        prompt = (f"Listing title: {row.get('title', '')[:200]}\n"
                  + (f"A text query that works on Xianyu for this brand (keep the same brand spelling): {hint}\n" if hint else "")
                  + "Look at the photo. Write ONE search query for Xianyu (闲鱼) that finds the same or very similar item: "
                  "brand as Chinese sellers write it, model or line name if recognizable, item type, main color and material — "
                  "mostly in Chinese, 3–6 words separated by spaces, no punctuation. Answer with the query only.")
        t = self._gen(client, [types.Part.from_bytes(data=img, mime_type="image/jpeg"), prompt]).text
        q = re.sub(r"[\"'“”「」【】,，.。:：]+", " ", t.strip().splitlines()[0])
        return re.sub(r"\s+", " ", q).strip()[:60]

    def ask(self, row: dict, question: str, history: list[dict]) -> str:
        """Уточняющий вопрос по фото в мини-окне: свободный ответ по-русски."""
        from google.genai import types
        s = load_settings()
        client = self._client(s)
        if client is None:
            raise RuntimeError(self.status["error"])
        img = _image(row["image"])
        ctx = (f"Объявление с Goofish: {row.get('title', '')[:200]}. Цена ¥{row.get('price_cny', 0):.0f}. "
               f"Описание: {(row.get('desc', '') + ' ' + row.get('labels', ''))[:500]}\n")
        talk = "".join(f"\n{'Вопрос' if h.get('role') == 'user' else 'Ответ'}: {h.get('text', '')[:800]}" for h in history[-6:])
        prompt = (ctx + "Ты помогаешь перекупщику мужского люкса и архива. Отвечай по-русски, коротко и по делу, "
                  "опираясь на фото; бренд в тексте мог быть указан неверно." + talk + f"\nВопрос: {question[:600]}")
        return self._gen(client, [types.Part.from_bytes(data=img, mime_type="image/jpeg"), prompt]).text.strip()

    def save(self, iid: str, res: dict | None, err: str = ""):
        with self.db_lock:
            self.db.execute("INSERT OR REPLACE INTO ai_checks VALUES(?,?,?,?,?)",
                            (iid, datetime.now().isoformat(timespec="seconds"), self.status.get("model", ""),
                             json.dumps(res, ensure_ascii=False) if res else "", err))
            self.db.commit()

    def _loop(self):
        from_err = 0
        while True:
            time.sleep(5)
            try:
                s = load_settings()
                if not (s.get("gemini_api_key") or "").strip():
                    self.status.update(enabled=False, error="нет ключа: впишите GEMINI_API_KEY в файл .env")
                    continue
                self.status["enabled"] = True
                if not self.status.get("model"):
                    self.status["model"] = s.get("gemini_model") or DEFAULTS["gemini_model"]
                if time.time() < self.status["pause_until"]:
                    continue
                with self.db_lock:
                    self.status["today"] = used_today(self.db)
                left = int(s.get("ai_daily_limit", 150)) - self.status["today"]
                self.status["limit"] = int(s.get("ai_daily_limit", 150))
                if left <= MANUAL_RESERVE:      # остаток — для ваших ручных проверок
                    self.status["error"] = "фоновая проверка на сегодня всё: остаток оставлен для кнопок «ИИ»/«Gemini»"
                    continue
                if time.time() - self.status.get("last_bg", 0) < BG_GAP:
                    continue
                self.status["last_bg"] = time.time()
                with _lock:
                    row = self.queue.pop(0) if self.queue else None
                if row is None:
                    continue
                try:
                    res = self.check(row)
                    err = ""
                    from_err = 0
                except Exception as e:  # сеть, лимит сервиса, ключ
                    res, err = None, repr(e)[:300]
                    code = getattr(e, "code", 0)
                    if "исчерпали лимит" in str(e) or (code == 429 and "PerDay" in str(e)):
                        self.status.update(error="все модели Gemini исчерпали дневной лимит — продолжу позже",
                                           pause_until=time.time() + 1800)
                        with _lock:
                            self.queue.insert(0, row)
                        continue
                    if code in (500, 503, 504):     # перегрузка сервиса: не тратим лимит, вещь — в конец очереди
                        self.status.update(error=f"сервис Gemini перегружен ({code}), повторю позже", pause_until=time.time() + 30)
                        with _lock:
                            self.queue.append(row)
                        continue
                    if code == 429:
                        self.status["pause_until"] = time.time() + 120
                        self.log("ИИ: сервис просит подождать (лимит), пауза 2 мин")
                        with _lock:
                            self.queue.insert(0, row)
                        continue
                    if code in (400, 401, 403) and "image" not in err.lower():
                        if "location is not supported" in err:     # Gemini не обслуживает страну, через которую идёт VPN
                            self.status.update(error="Gemini не работает из страны вашего VPN — смените сервер VPN",
                                               pause_until=time.time() + 600)
                            self.log("ИИ: Gemini не работает из страны, через которую идёт VPN, — смените сервер VPN (ключ в порядке)")
                        else:
                            self.status.update(error=f"ключ или доступ не работает ({code})", pause_until=time.time() + 600)
                            self.log(f"ИИ: ошибка доступа {code} — проверьте ключ в settings.json")
                        with _lock:
                            self.queue.insert(0, row)
                        continue
                    from_err += 1
                    if from_err >= 5:
                        self.status["pause_until"] = time.time() + 300
                self.status["error"] = err
                self.save(row["id"], res, err)
                if res:
                    self.log(f"ИИ: {row.get('title', '')[:30]} → {res.get('brand') or '?'} / {res.get('item')} / риск {res.get('fake_risk')}")
                    self.on_result(row["id"])
            except Exception as e:
                self.status["error"] = repr(e)[:200]
