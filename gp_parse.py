"""
Goofish (闲鱼 / Xianyu) parser PRO: мультибрендовый поиск «грааля» для перепродажи.
Чистит шлак и ставит каждой вещи общую оценку из 5 частей: выгода, ликвидность,
состояние, желанность, надёжность (риск подделки).

ОСНОВНОЙ РЕЖИМ: РАСШИРЕНИЕ ДЛЯ CHROME (без копирования). Один раз установите папку chrome_ext
(chrome://extensions -> «Режим разработчика» -> «Загрузить распакованное расширение»), затем:
    python goofish_parser_pro.py --listen
и просто листайте поиск Goofish в своём обычном Chrome. Расширение передаёт скрипту объявления
со страниц, которые вы открыли сами (со ссылками и фото), а при открытии карточки и её описание.
Живой отчёт: http://127.0.0.1:8765/ (или щелчок по значку расширения): фото, фильтры,
кнопки «★ интересно» и «✕ не то». Значок: число объявлений, «off» = скрипт не запущен,
«!» = сайт сообщил об ограничении (сделайте паузу). Остановка: Ctrl+C.

КАТАЛОГ catalog.json (рядом со скриптом): категории (сумки, обувь, кеды, верхняя одежда...),
бренды с китайскими названиями, модели, варианты (материал, линейка, эпоха), оценки желанности
и ликвидности, пороги цен по бренду и типу, цены продажи в ₽ и каналы продаж с комиссиями.
Правьте его свободно: код менять не нужно. Фильтры по нему:
    --brands prada,golden_goose      (ключи брендов из каталога)
    --types bag,sneakers             (ключи категорий из каталога)

БАЗА out/items.db: все объявления копятся между запусками (история цены, ваши отметки).
Отчёт показывает всю базу; «новое» и «цена снижена» считаются относительно текущего запуска.
--only-new оставит только их. --reset-seen начнёт базу заново (старая сохранится как .bak).

РАЗБОР ВСТАВЛЕННОГО ТЕКСТА: в обычном браузере Ctrl+A, Ctrl+C на странице выдачи, вставьте
в файл, затем:
    python goofish_parser_pro.py --paste paste1.txt --paste paste2.txt
Ссылок и фото в скопированном тексте нет.

СТАРЫЙ РЕЖИМ (управляемый браузер, --preset / -q): сайт его распознаёт и блокирует, оставлен
только для совместимости. Защита аккаунта: при коде ограничения парсер останавливается
и блокирует запуск на --cooldown-hours.

Результат: папка out/: best.html (отчёт), best.csv (прошло фильтры, по оценке),
best_market.csv / best_low.csv (категории A и B), all_items.csv (вся база), rejected.csv
(что и почему отсеяно). Категория B: цена ниже порога бренда/типа, но есть сигналы
реальной вещи (чек, готовность к экспертизе...). Это кандидаты на ручную проверку.

Расчёт выгоды: цены продажи в ₽ берутся из catalog.json (sell_rub у модели), из --prices
prices.json ({"Нейлоновый мессенджер": 25000, "Prada": 20000}) или --default-sell.
Курс и расходы: --rate, --intl, --ru, --photo, --expert, --other, --target.

ВАЖНО:
 * Скрипт не определяет подлинность. Он убирает очевидный мусор и ранжирует.
 * Папку profile никому не отправляйте: там сессия старого режима.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import math
import random
import re
from gp_log import log_dir, parse_error, run_add, run_auth, run_summary, safe_parse, say  # noqa: F401
import statistics
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from html import escape as html_escape
from itertools import product
from pathlib import Path
from urllib.parse import quote

UI_DIR = Path(__file__).with_name("ui")


def ui_text(name: str) -> str:
    """Шаблон страницы из папки ui/: HTML, CSS и JS лежат отдельно от кода."""
    return (UI_DIR / name).read_bytes().decode("utf-8")

# Коды/тексты ответов, по которым сайт сообщает об ограничении или проверке.
# Список из практики и НЕПОЛНЫЙ. Задача не обходить их, а вовремя остановиться.
THROTTLE_SIGNALS = ["FAIL_SYS_USER_VALIDATE", "RGV587", "被挤爆", "ILLEGAL_ACCESS", "ACCESS_DENIED"]
LOGIN_URL_HINTS = ["login", "punish", "captcha"]

API_MARKERS = ("mtop.taobao.idlemtopsearch.pc.search",)
FEED_MARKERS = ("idlehome", "feed", "recommend", "guess",   # «猜你喜欢» и прочие рекомендации
                "xyh", "item.list", "personal", "user.page")  # вещи в профиле продавца
DETAIL_MARKERS = ("mtop.taobao.idle.pc.detail",)

# ---------------------------------------------------------------- словари
# Общие для всех брендов. Бренды, модели, категории и пороги цен лежат в catalog.json.
FAKE_WORDS = [
    "高仿", "精仿", "原单", "尾单", "复刻", "a货", "超a", "同款", "仿", "1:1",
    "平替", "类似", "工厂", "批发", "微商", "代工", "跟单", "厂货",
    "一比一", "顶级版", "顶级复刻", "顶级原单", "莆田", "外贸", "仿真",   # просто «顶级» — «высший сорт», см. RISK_WORDS
    "开模", "定织", "源头", "顶版", "高版本",
    # жаргон продавцов реплик: «фабричная бирка», «чистый оригинал», «уровень компании», «сравнивайте детали»
    "纯原", "公司级", "钢标", "顶品", "最顶", "市场货", "拼图对比", "对版", "货源", "1比1", "1：1", "原版开发",
    "非正品", "非正", "不是正品",
    # «версия с чипом», «фабричная бирка», «лучшая версия в сети», «отличается от рыночных» — язык реплик
    "芯片版", "nfc芯片版", "原厂标", "原c标", "最顶版本", "全网最顶", "区别市场", "顶级版本",
    # «высший сорт фабричного качества», «фабричная кожа как у оригинала», «лучший товар на рынке»
    "特级原厂", "原厂牛皮", "原厂皮", "原厂品质", "最高品质", "市面最好", "对版原厂", "工厂直发", "独家实拍", "特级品质",
    "莞产",   # «сделано в Дунгуане» — фабрики реплик
]
# Слабые признаки реплики: по одному ничего не значат, вместе поднимают риск (см. fake_risk)
RISK_WORDS = ["原厂", "细节对比", "支持对比", "专柜品质", "品质", "做工精细", "做工精致", "高品质", "代工厂",
              "工厂店", "进口面料", "进口五金", "细节图", "现货", "多色",
              # переписанное описание с сайта бренда — типично для продавцов новых реплик
              "这款", "彰显", "诠释", "融合", "相得益彰", "隽永", "货号", "已过检", "已过验", "闭眼入", "爆款",
              "实物图", "惊艳", "低调奢华", "男女同款", "原厂", "顶级"]
# Ник продавца-магазина: «只做Bv包» (только BV), «专营», «工厂», «货源»
SHOP_NICK_WORDS = ["只做", "专营", "专卖", "工厂", "货源", "批发", "源头", "代购", "精品", "奢品", "大牌", "名品",
                   "定制", "皮具", "白云", "专供"]   # «广州白云皮具定制»: Байюнь (Гуанчжоу) — рынок реплик сумок
# из них однозначные: такой ник у продавца люкса — отсев без разговоров
SHOP_NICK_STRONG = ["只做", "工厂", "货源", "批发", "源头", "白云", "皮具定制", "专供"]
# Признаки, что вещь проверяли или есть документы
PROOF_WORDS = ["验货宝", "支持验货", "可验货", "小票", "发票", "购买凭证", "专柜小票", "保卡", "鉴定证书", "鉴定报告"]
# Не вещь, а услуга/предзаказ/посредник (актуально для вставленного текста выдачи)
NON_RESALE_WORDS = ["定金", "预售", "代拍", "代购", "可代发", "店铺直发"]
SERVICE_OK_PHRASES = ["支持鉴定", "可鉴定", "接受鉴定", "接受第三方鉴定", "支持验货", "可验货"]
PROVINCES = {
    "北京", "上海", "天津", "重庆", "河北", "山西", "辽宁", "吉林", "黑龙江", "江苏", "浙江", "安徽",
    "福建", "江西", "山东", "河南", "湖北", "湖南", "广东", "海南", "四川", "贵州", "云南", "陕西",
    "甘肃", "青海", "台湾", "内蒙古", "广西", "西藏", "宁夏", "新疆", "香港", "澳门",
}
NON_CITY = {"全新", "通用", "其他", "自用", "二手", "未使用"}
SELLER_TAG_RE = re.compile(r"(信用|好评|极快|超快|回复|发货)")

# Наборы запросов для --preset (старый режим прямого сбора)
GRAIL_TERMS = [
    "vintage", "古着 老款", "绝版", "限量", "联名", "秀款", "走秀", "特别版",
    "鳄鱼皮", "鸵鸟皮", "蜥蜴皮", "蟒蛇皮", "90年代", "80年代", "Linea Rossa",
    "Adidas", "archive", "老款 正品", "停产",
]

GRAIL_TERMS_EXTRA = [
    "y2k", "千禧", "稀有", "孤品", "典藏", "编号 限量", "秀场", "Fendi", "古董",
    "Nappa Gaufre", "Bauletto", "Cahier", "Odette", "Sidonie",
]
PRESET_MODEL_QUERIES_EXTRA = [
    "Nappa Gaufre", "Bauletto", "Cahier", "Odette", "Sidonie", "Esplanade", "Brique", "Fairy",
]

PRESET_MODEL_QUERIES = [
    "Re-Edition 2005", "Re-Nylon", "Galleria", "Cleo", "Symbole", "Buckle",
    "Arque", "三角标 尼龙", "水饺包", "邮差包", "托特包", "腋下包", "尼龙 斜挎包",
]
PRESET_TYPE_QUERIES = [
    "单肩包", "手提包", "斜挎包", "双肩包", "水桶包", "链条包", "手拿包", "饺子包",
    "女包 正品", "专柜 正品 包", "二手 包",
]


def build_preset(name: str, brand: str = "both") -> list[str]:
    brands = {"both": ["普拉达", "Prada"], "cn": ["普拉达"], "en": ["Prada"]}[brand]
    if name == "prada-grail":
        parts = GRAIL_TERMS
    elif name == "prada-grail-plus":
        parts = GRAIL_TERMS + GRAIL_TERMS_EXTRA
    elif name == "prada":
        parts = PRESET_MODEL_QUERIES
    elif name == "prada-wide":
        parts = PRESET_MODEL_QUERIES + PRESET_MODEL_QUERIES_EXTRA + PRESET_TYPE_QUERIES
    else:
        sys.exit(f"Неизвестный preset: {name}")
    out = []
    for b, m in product(brands, parts):
        q = f"{b} {m}"
        if q not in out:
            out.append(q)
    return out


# ---------------------------------------------------------------- утилиты
def dig(obj, *path, default=""):
    for key in path:
        if isinstance(obj, dict):
            obj = obj.get(key)
        else:
            return default
        if obj is None:
            return default
    return obj


def find_key(obj, keys, depth=0):
    """Рекурсивно ищет первое непустое значение по любому из ключей."""
    if depth > 8:
        return None
    if isinstance(obj, dict):
        for k in keys:
            v = obj.get(k)
            if isinstance(v, (str, int, float)) and str(v).strip():
                return v
        for v in obj.values():
            r = find_key(v, keys, depth + 1)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = find_key(v, keys, depth + 1)
            if r is not None:
                return r
    return None


def cooldown_left(path: Path) -> float:
    """Сколько часов осталось до конца паузы после ограничения (0 = можно работать)."""
    try:
        until = json.loads(path.read_text(encoding="utf-8")).get("until", 0)
    except Exception:
        return 0.0
    return max(0.0, (until - time.time()) / 3600)


def set_cooldown(path: Path, hours: float, reason: str):
    path.write_text(json.dumps({"until": time.time() + hours * 3600, "reason": reason[:300]},
                               ensure_ascii=False), encoding="utf-8")


def read_text_auto(path: Path) -> str:
    """Читает файл, определяя кодировку (UTF-8 / UTF-8 с BOM / UTF-16 из Блокнота / GBK)."""
    raw = path.read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace")
    for enc in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def describe_paste_problem(path: Path, text: str):
    size = path.stat().st_size
    lines = text.split("\n")
    rub = sum(1 for l in lines if l.strip().startswith("¥"))
    say(f"  Диагностика {path.name}: размер {size} байт, строк {len(lines)}, строк с «¥»: {rub}")
    if size == 0:
        say("  Файл ПУСТОЙ. Если вы вставляли текст в Блокнот, сохраните файл (Ctrl+S) и запустите снова.")
    elif rub == 0:
        say("  В файле нет цен («¥»). Возможно, скопирована не страница выдачи или включён автоперевод.")
    else:
        say("  Цены есть, но разобрать не получилось. Пришлите мне первые ~60 строк файла.")


def parse_paste_text(text: str, source: str = "") -> dict:
    """Разбирает текст, скопированный (Ctrl+A, Ctrl+C) со страницы выдачи Goofish в обычном браузере.
    Объявление = заголовок (+теги) -> цена ("¥", целое, ".дробь") -> мета (хотят, снижение, город, продавец)."""
    lines = [l.strip() for l in text.replace("\r", "").split("\n")]
    n = len(lines)
    def price_at(i):
        l = lines[i]
        m = re.fullmatch(r"¥\s*(\d[\d,]*(?:\.\d+)?)", l)
        if m:
            return float(m.group(1).replace(",", "")), i + 1
        if l == "¥" and i + 1 < n and re.fullmatch(r"\d[\d,]*", lines[i + 1]):
            val, j = lines[i + 1], i + 2
            if j < n and re.fullmatch(r"\.\d+", lines[j]):
                val, j = val + lines[j], j + 1
            return float(val.replace(",", "")), j
        return None

    first_price = next((k for k in range(n) if price_at(k) is not None), n)
    start = 0
    for k, l in enumerate(lines[:first_price]):
        if re.fullmatch(r"\d+/\d+", l):
            start = k + 1

    items: dict[str, dict] = {}
    block_end, i = start, start
    while i < n:
        pr = price_at(i)
        if pr is None:
            i += 1
            continue
        title_lines = [l for l in lines[block_end:i] if l]
        if not title_lines:           # «¥8.88» (старая цена) или мусор без заголовка
            i = pr[1]
            continue
        price, j = pr
        want, drop, posted, city, seller, orig = 0, "", "", "", "", ""
        pre_posted = [l for l in title_lines if "前发布" in l and len(l) <= 12]
        if pre_posted:
            posted = pre_posted[0]
            title_lines = [l for l in title_lines if l not in pre_posted] or title_lines
        while j < n:
            l = lines[j]
            if not l:
                j += 1
                continue
            m = re.search(r"(\d+)\s*人想要", l)
            if m:
                want = int(m.group(1)); j += 1; continue
            if "累计降价" in l:
                drop = l; j += 1; continue
            if "前发布" in l:
                posted = l; j += 1; continue
            if re.fullmatch(r"¥\s*\d+(?:\.\d+)?", l):
                orig = l; j += 1; continue
            if not city and (l in PROVINCES or (re.fullmatch(r"[\u4e00-\u9fff]{2,4}", l)
                                                  and l not in NON_CITY and "新" not in l)):
                city = l; j += 1; continue
            if city and not seller and len(l) <= 8 and SELLER_TAG_RE.search(l):
                seller = l; j += 1
            break
        block_end, i = j, j
        title = max(title_lines, key=len)
        tags = [t for t in title_lines if t is not title]
        iid = "p" + hashlib.md5(f"{title[:80]}|{price}|{city}".encode("utf-8")).hexdigest()[:10]
        if iid in items:
            continue
        items[iid] = {
            "id": iid, "title": title, "price_cny": price, "city": city, "seller": seller,
            "want": want, "free_ship": "包邮" in title, "image": "", "url": "", "desc": "",
            "queries": f"paste:{source}", "tags": " | ".join(tags), "posted": posted, "drop": drop,
        }
    return items


def to_float(s) -> float:
    m = re.search(r"\d+(?:\.\d+)?", str(s).replace(",", ""))
    return float(m.group()) if m else 0.0


def low(s: str) -> str:
    return (s or "").lower()


def fmt_age(iso: str) -> str:
    """«25 мин», «3 ч», «2 дн» — сколько прошло с момента iso (время публикации объявления)."""
    try:
        m = (datetime.now() - datetime.fromisoformat(iso)).total_seconds() / 60
    except (TypeError, ValueError):
        return ""
    return f"{max(0, round(m))} мин" if m < 60 else f"{round(m / 60)} ч" if m < 48 * 60 else f"{round(m / 1440)} дн"


def has_any(text: str, words) -> list[str]:
    t = low(text)
    return [w for w in words if low(w) in t]


LABEL_SKIP = ("验货宝", "卖家信用", "买家信用", "内发布", "前发布", "降价", "包邮", "人想要", "品牌专柜", "超赞", "回头客")


def list_labels(entry) -> str:
    """Серые метки под ценой в выдаче («YSL/圣罗兰 | 轻微使用痕迹»): бренд из поля карточки виден сразу,
    без открытия объявления. Метка с «/» — бренд."""
    found = []

    def walk(o, depth=0):
        if depth > 14:
            return
        if isinstance(o, dict):
            if "LabelSystem2.0" in o and isinstance(o.get("content"), str):
                t = o["content"].strip()
                if (t and len(t) <= 30 and not any(s in t for s in LABEL_SKIP) and t not in found
                        and not t.endswith("Icon") and t != "你交易过的"):
                    found.append(t)
            for v in o.values():
                walk(v, depth + 1)
        elif isinstance(o, list):
            for v in o:
                walk(v, depth + 1)
    walk(entry)
    brand = [t for t in found if "/" in t and not re.search(r"\d", t)]
    rest = [t for t in found if t not in brand]
    return " | ".join([f"品牌: {b}" for b in brand[:1]] + rest[:3])


def parse_item(entry: dict) -> dict | None:
    main = dig(entry, "data", "item", "main", default={})
    ex = main.get("exContent", {}) if isinstance(main, dict) else {}
    if not ex:
        return None
    click = dig(main, "clickParam", "args", default={})
    detail = ex.get("detailParams", {}) if isinstance(ex.get("detailParams"), dict) else {}
    item_id = str(ex.get("itemId") or detail.get("itemId") or click.get("item_id", ""))
    if not item_id:
        return None

    price = detail.get("soldPrice") or click.get("price")
    if not price:
        raw = ex.get("price")
        if isinstance(raw, list):
            raw = "".join(str(p.get("text", "")) for p in raw if isinstance(p, dict))
        price = raw
    blob = json.dumps(entry, ensure_ascii=False)
    m = re.search(r"(\d+)\s*人想要", blob)
    pub = str(click.get("publishTime") or "")   # когда выложено, мс с 1970 — по нему «свежесть» и ночь в Китае
    return {
        "id": item_id,
        "title": ex.get("title") or detail.get("title", ""),
        "price_cny": to_float(price),
        "city": ex.get("area", ""),
        "seller": ex.get("userNickName") or detail.get("userNick", ""),
        "seller_id": str(find_key(entry, ["userId", "sellerId", "seller_id"]) or ""),
        "want": int(m.group(1)) if m else int(to_float(click.get("wantNum") or 0)),
        "posted": datetime.fromtimestamp(int(pub) / 1000).isoformat(timespec="seconds") if pub.isdigit() else "",
        "free_ship": "包邮" in blob,
        "tags": "验货宝" if "验货宝" in blob else "",   # Goofish проверяет вещь до отправки покупателю
        "image": ex.get("picUrl") or detail.get("picUrl", ""),
        "url": f"https://www.goofish.com/item?id={item_id}",
        "desc": "",
        "labels": list_labels(entry),
        "queries": "",
    }


FOLLOW_MARKERS = ("follow", "attention", "fans")    # список подписок (关注) / подписчиков (粉丝) в профиле


def follow_users(data) -> list[tuple[str, str]]:
    """Люди из списка подписок: словари с числовым id и ником, по порядку, без повторов."""
    out, seen = [], set()

    def walk(o, depth=0):
        if depth > 12:
            return
        if isinstance(o, dict):
            uid = next((str(o[k]) for k in ("userId", "kcUserId", "followUserId", "targetUserId", "uid")
                        if str(o.get(k) or "").isdigit() and len(str(o[k])) >= 6), "")
            nick = next((str(o[k]) for k in ("nick", "userNick", "displayName", "nickName", "nickname", "userName")
                         if isinstance(o.get(k), str) and o[k].strip()), "")
            if uid and nick and uid not in seen:
                seen.add(uid)
                out.append((uid, nick.strip()[:40]))
            for v in o.values():
                walk(v, depth + 1)
        elif isinstance(o, list):
            for v in o:
                walk(v, depth + 1)
    walk(data)
    return out


# проданная вещь в списке профиля («综合» показывает и 已售出): статус в карточке списка. Точное поле сайта ещё
# не сверено — образцы ответов профиля пишутся в out/debug_profile_list_*.json
SOLD_RX = re.compile(r'已售出|已卖出|卖掉了|"itemStatus"\s*:\s*"?[1-9]')


def parse_any(entry: dict) -> dict | None:
    """Карточка из рекомендаций: формат как в поиске или любой другой, где есть id, название и цена."""
    row = parse_item(entry)
    if row or not isinstance(entry, dict):
        if row and SOLD_RX.search(json.dumps(entry, ensure_ascii=False)):
            row["sold"] = True
        return row
    iid = find_key(entry, ["itemId", "item_id"])
    title = find_key(entry, ["title", "itemTitle"])
    price = find_key(entry, ["soldPrice", "price", "priceText", "showPrice"])
    blob = json.dumps(entry, ensure_ascii=False)
    if price is None:   # цена списком кусков [{"text": "¥"}, {"text": "890"}]
        pm = re.search(r'"price"\s*:\s*\[[^\]]*?"text"\s*:\s*"(\d+(?:\.\d+)?)"', blob)
        price = pm.group(1) if pm else None
    if not iid or not title or not to_float(price):
        return None
    m = re.search(r"(\d+)\s*人想要", blob)
    pic = find_key(entry, ["picUrl", "mainPic", "mainPicUrl", "pic", "imageUrl", "imgUrl", "coverUrl", "img"]) or ""
    if not pic:   # в «猜你喜欢» фото лежит в поле "url" среди иконок: берём загруженное фото вещи, не аватар
        pm = re.search(r'"url"\s*:\s*"((?:https?:)?//img\.alicdn\.com/bao/uploaded/[^"]+?(?:xy_item|fleamarket)[^"]*)"', blob)
        pic = pm.group(1) if pm else ""
    return {"id": str(iid), "title": str(title), "price_cny": to_float(price), **({"sold": True} if SOLD_RX.search(blob) else {}),
            "city": str(find_key(entry, ["area", "city"]) or ""),
            "seller": str(find_key(entry, ["userNickName", "userNick", "nick"]) or ""),
            "seller_id": str(find_key(entry, ["userId", "sellerId", "seller_id"]) or ""),
            "want": int(m.group(1)) if m else 0, "free_ship": "包邮" in blob,
            "tags": "验货宝" if "验货宝" in blob else "", "image": str(pic),
            "url": f"https://www.goofish.com/item?id={iid}", "desc": "", "labels": list_labels(entry), "queries": ""}


PROP_NAMES = ("品牌", "成色", "尺码", "款式", "适用季节", "材质", "购买渠道", "附件清单")


def item_props(data) -> str:
    """Свойства из карточки (品牌/成色/尺码…): ищем словари, где одно значение — имя свойства, другое — его значение."""
    found = {}

    def walk(o, depth=0):
        if depth > 12:
            return
        if isinstance(o, dict):
            vals = [v for v in o.values() if isinstance(v, str) and v.strip()]
            name = next((v.strip().rstrip("：:") for v in vals if v.strip().rstrip("：:") in PROP_NAMES), "")
            if name and name not in found:
                rest = [v.strip() for v in vals if v.strip().rstrip("：:") != name and len(v) < 60
                        and not v.startswith(("http", "//"))]
                if rest:
                    found[name] = rest[0]
            for v in o.values():
                walk(v, depth + 1)
        elif isinstance(o, list):
            for v in o:
                walk(v, depth + 1)
    walk(data)
    return " | ".join(f"{k}: {v}" for k, v in found.items())


def parse_detail(data: dict) -> tuple[dict | None, dict, list[dict]]:
    """Карточка товара: объявление (поля как из поиска), продавец и его другие вещи под карточкой.
    Продавец: числовой id (по нему открывается профиль; в поиске id шифрованный), дней на 闲鱼, отзывы, продано."""
    d = dig(data, "data", default={})
    it, se = (d.get("itemDO") or {}, d.get("sellerDO") or {}) if isinstance(d, dict) else ({}, {})
    if not isinstance(it, dict) or not isinstance(se, dict):
        return None, {}, []
    rm = se.get("remarkDO") if isinstance(se.get("remarkDO"), dict) else {}
    uid = str(se.get("sellerId") or dig(d, "trackParams", "sellerId", default="") or "")
    nick = str(se.get("nick") or se.get("uniqueName") or "")
    seller = {"uid": uid, "nick": nick, "days": int(to_float(se.get("userRegDay"))),
              "sold": int(to_float(se.get("hasSoldNumInteger"))), "items": int(to_float(se.get("itemCount"))),
              "good": int(to_float(rm.get("sellerGoodRemarkCnt"))), "bad": int(to_float(rm.get("sellerBadRemarkCnt"))),
              "city": str(se.get("city") or ""), "visit": str(se.get("lastVisitTime") or ""),
              "reply": str(se.get("replyRatio24h") or "")} if uid else {}
    iid = str(it.get("itemId") or "")
    row = None
    if iid:
        blob = json.dumps(it, ensure_ascii=False)
        pub = str(it.get("gmtCreate") or "")
        row = {"id": iid, "title": str(it.get("title") or ""), "price_cny": to_float(it.get("soldPrice")),
               "city": seller.get("city", ""), "seller": nick, "seller_id": uid,
               "want": int(to_float(it.get("wantCnt"))),
               "posted": datetime.fromtimestamp(int(pub) / 1000).isoformat(timespec="seconds") if pub.isdigit() else "",
               "free_ship": "包邮" in blob,
               "tags": "验货宝" if "验货宝" in blob else "",
               "image": str(dig(d, "trackParams", "mainPic", default="") or ""),
               "url": f"https://www.goofish.com/item?id={iid}", "desc": "", "labels": "", "queries": ""}
    others = []
    for x in se.get("sellerItems") or []:
        if isinstance(x, dict) and x.get("itemId") and to_float(str(x.get("text", "")).lstrip("¥")):
            oid = str(x["itemId"])
            others.append({"id": oid, "title": str(x.get("title") or ""), "price_cny": to_float(str(x["text"]).lstrip("¥")),
                           "city": seller.get("city", ""), "seller": nick, "seller_id": uid, "want": 0, "posted": "",
                           "free_ship": dig(x, "attributeMap", "freeShipping", default="") == "1", "tags": "",
                           "image": str(x.get("iconUrl") or ""), "url": f"https://www.goofish.com/item?id={oid}",
                           "desc": "", "labels": "", "queries": ""})
    return row, seller, others


def extract_items(data, depth=0) -> list[dict]:
    """Все карточки из любого списка в ответе (лента рекомендаций устроена иначе, чем поиск)."""
    out = []
    if depth > 7:
        return out
    if isinstance(data, list):
        rows = [parse_any(x) for x in data if isinstance(x, dict)]
        rows = [r for r in rows if r]
        if len(rows) >= 2:
            return rows
        for x in data:
            out += extract_items(x, depth + 1)
    elif isinstance(data, dict):
        for v in data.values():
            out += extract_items(v, depth + 1)
    return out


# ---------------------------------------------------------------- каталог
def load_catalog(path: Path) -> dict:
    try:
        cat = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        sys.exit(f"Нет файла каталога {path}: он должен лежать рядом со скриптом.")
    except json.JSONDecodeError as e:
        sys.exit(f"Ошибка в {path}: строка {e.lineno}, столбец {e.colno}: {e.msg}")
    own = {low(al) for b in cat["brands"].values() for al in b["aliases"]}
    cat["other_brands"] = [w for w in cat.get("other_brands", []) if low(w) not in own]
    return cat


def validate_catalog(c) -> list[str]:
    """Проверка каталога из редактора до записи: пустой список = можно сохранять."""
    err = []
    ru = {"desire": "желанность", "liquidity": "ликвидность", "min_price": "мин. цена",
          "low_floor": "абсолютный минимум", "penalty": "штраф"}
    num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)
    words = lambda v: isinstance(v, list) and v and all(isinstance(w, str) and w.strip() for w in v)
    if not isinstance(c, dict) or not isinstance(c.get("categories"), dict) or not isinstance(c.get("brands"), dict):
        return ["каталог повреждён: нет категорий или брендов"]
    for k in ("weights", "channels"):
        if k not in c:
            err.append(f"нет раздела {k}")
    cats = c["categories"]
    for key, ct in cats.items():
        where = f"Категория «{ct.get('name') or key}»"
        if not str(ct.get("name", "")).strip():
            err.append(f"{where}: пустое название")
        if not words(ct.get("words")):
            err.append(f"{where}: нужно хотя бы одно слово")
        for f in ("min_price", "low_floor"):
            if f in ct and not (num(ct[f]) and ct[f] >= 0):
                err.append(f"{where}: поле «{ru[f]}» должно быть числом ≥ 0")
        for st in ct.get("subtypes", []):
            if not str(st.get("name", "")).strip() or not words(st.get("words")) or not num(st.get("desire", 0)):
                err.append(f"{where}: подтип «{st.get('name', '')}» без названия, слов или с нечисловым весом")
    for key, b in c["brands"].items():
        where = f"Бренд «{b.get('name') or key}»"
        if not str(b.get("name", "")).strip():
            err.append(f"{where}: пустое название")
        if not words(b.get("aliases")):
            err.append(f"{where}: нужно хотя бы одно написание")
        for f in ("desire", "liquidity"):
            if not (num(b.get(f)) and 0 <= b[f] <= 100):
                err.append(f"{where}: поле «{ru[f]}» должно быть числом 0–100")
        for k, v in {**b.get("min_price", {}), **b.get("category_desire", {})}.items():
            if not num(v):
                err.append(f"{where}: значение для «{k}» должно быть числом")
        for k, v in b.get("sell_rub", {}).items():
            if not (num(v) and v > 0):
                err.append(f"{where}: цена по умолчанию для «{cats.get(k, {}).get('name', k)}» должна быть положительным числом")
        for k in b.get("ambassador", []):
            if k not in cats:
                err.append(f"{where}: амбассадор в неизвестной категории «{k}»")
        for i, m in enumerate(b.get("models", []), 1):
            mw = f"{where}, модель {i} «{m.get('name', '')}»"
            if not str(m.get("name", "")).strip():
                err.append(f"{mw}: пустое название")
            if not (words(m.get("words")) or (isinstance(m.get("all"), list) and m["all"] and all(words(g) for g in m["all"]))):
                err.append(f"{mw}: нужны слова для распознавания")
            if m.get("category") and m["category"] not in cats:
                err.append(f"{mw}: неизвестная категория «{m['category']}»")
            for f in ("desire", "liquidity"):
                if f in m and not (num(m[f]) and 0 <= m[f] <= 100):
                    err.append(f"{mw}: поле «{ru[f]}» должно быть числом 0–100")
            if m.get("sell_rub") is not None and not (num(m["sell_rub"]) and m["sell_rub"] > 0):
                err.append(f"{mw}: цена продажи должна быть положительным числом")
            if m.get("min_price") is not None and not (num(m["min_price"]) and m["min_price"] >= 0):
                err.append(f"{mw}: поле «{ru['min_price']}» должно быть числом ≥ 0")
        for v in b.get("variants", []):
            if not str(v.get("name", "")).strip() or not words(v.get("words")) or not num(v.get("desire", 0)):
                err.append(f"{where}: вариация «{v.get('name', '')}» без названия, слов или с нечисловым весом")
    o = c.get("outside")
    if o is not None:
        for b in o.get("interesting", []):
            if not str(b.get("name", "")).strip() or not words(b.get("words")) or not (num(b.get("desire")) and 0 <= b["desire"] <= 100):
                err.append(f"Вне каталога: бренд «{b.get('name', '')}» без названия, слов или с желанностью не 0–100")
        if not isinstance(o.get("junk", []), list) or not all(isinstance(w, str) and w.strip() for w in o.get("junk", [])):
            err.append("Вне каталога: список «шлак» должен состоять из слов")
        for f in ("min_price", "unknown_min_price"):
            if f in o and not (num(o[f]) and o[f] >= 0):
                err.append(f"Вне каталога: порог цены «{f}» должен быть числом ≥ 0")
    for sect in ("modifiers", "rarity"):
        for m in c.get(sect, []):
            if not str(m.get("name", "")).strip() or not words(m.get("words")):
                err.append(f"Правило «{m.get('name', '')}»: нужно название и хотя бы одно слово")
            for f in ("desire", "penalty"):
                if f in m and not num(m[f]):
                    err.append(f"Правило «{m.get('name', '')}»: поле «{ru[f]}» должно быть числом")
    return err


def dump_catalog(c: dict) -> str:
    """JSON с отступами, но короткие списки слов в одну строку: файл остаётся читаемым руками."""
    text = json.dumps(c, ensure_ascii=False, indent=2)
    flat = lambda m: "[" + ", ".join(x.strip() for x in m.group(1).split(",\n")) + "]"
    return re.sub(r"\[\n\s*((?:\"(?:[^\"\\]|\\.)*\"|-?\d+(?:\.\d+)?|true|false|null)"
                  r"(?:,\n\s*(?:\"(?:[^\"\\]|\\.)*\"|-?\d+(?:\.\d+)?|true|false|null))*)\n\s*\]", flat, text) + "\n"


def save_catalog(path: Path, new: dict, keep: int = 10) -> Path:
    """Резервная копия текущего файла, затем запись. Старые копии сверх keep удаляются."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.stem}.backup-{stamp}.json")
    if path.exists():
        backup.write_bytes(path.read_bytes())
    tmp = path.with_suffix(".tmp")
    tmp.write_text(dump_catalog(new), encoding="utf-8")
    tmp.replace(path)
    for old in sorted(path.parent.glob(f"{path.stem}.backup-*.json"))[:-keep]:
        old.unlink()
    return backup


_WORD_RX: dict[str, tuple[str, re.Pattern | None]] = {}


def find_pos(text: str, word: str) -> int:
    """Позиция слова в тексте (уже в нижнем регистре) или -1. Латиница ищется как отдельное
    слово (ring не найдётся в spring, prada найдётся в prada包), иероглифы как подстрока.
    Вызывается миллионы раз за пересчёт: слово разбирается один раз, а регулярка запускается,
    только если слово вообще есть в тексте."""
    w, rx = _WORD_RX.get(word) or _word_rx(word)
    if rx is None:
        return text.find(w)
    if w not in text:
        return -1
    m = rx.search(text)
    return m.start() if m else -1


def _word_rx(word: str) -> tuple[str, re.Pattern | None]:
    w = low(word)
    rx = (re.compile(r"(?<![a-z0-9])" + re.escape(w) + r"(?:e?s)?(?![a-z0-9])")
          if w.isascii() and re.search(r"[a-z]", w) else None)
    _WORD_RX[word] = (w, rx)
    return w, rx


def hits(text: str, words) -> list[str]:
    out = []
    for word in words:      # то же, что find_pos(text, word) >= 0, но без вызова функции на каждое слово
        w, rx = _WORD_RX.get(word) or _word_rx(word)
        if w in text and (rx is None or rx.search(text)):
            out.append(word)
    return out


def clamp(v: float) -> int:
    return int(max(0, min(100, round(v))))


def detect_brand(title: str, text: str, cat: dict) -> tuple[str, str]:
    """(ключ бренда, "") или ("", причина отсева). Решает бренд, названный в тексте раньше всех."""
    found = {}
    for key, b in cat["brands"].items():
        ps = [p for p in (find_pos(text, al) for al in b["aliases"]) if p >= 0]
        if ps:
            found[key] = min(ps)
    others = sorted((p, w) for p, w in ((find_pos(text, w), w) for w in cat["other_brands"]) if p >= 0)
    collab = has_any(text, cat.get("collab_words", []))
    for k in list(found):       # «Junya Watanabe MAN by Comme des Garçons»: материнский бренд в названии — не второй бренд
        for inc in cat["brands"].get(k, {}).get("includes", []):
            found.pop(inc, None)
    if not found:
        return "", "бренд не из каталога" + (f" ({others[0][1]})" if others else "")
    key = min(found, key=found.get)
    if others and others[0][0] < found[key] and not collab:
        return "", "чужой бренд: " + others[0][1]
    in_title = [k for k, p in found.items() if p < len(title)]
    # «Boris Bidjan Saberi … (далеко дальше) undercover / yohji / number nine» — список имён для поиска, а не коллаборация:
    # первый бренд в начале названия, остальные не ближе 40 знаков от него
    first = found[key]
    seo = first < 40 and all(found[k] - first >= 40 for k in in_title if k != key)
    if len(in_title) > 1 and not collab and not seo:
        return "", "несколько брендов: " + ", ".join(cat["brands"][k]["name"] for k in in_title)
    return key, ""


def detect_model(text: str, brand: dict, title: str | None = None) -> dict | None:
    """«strict»-модели (тип одежды: свитер, худи…) ищутся только в названии — в описании куртки бывает «针织 подкладка»."""
    for m in brand.get("models", []):
        if m.get("strict") and title is not None:
            if m.get("words") and hits(title, m["words"]) or m.get("all") and all(hits(title, g) for g in m["all"]):
                return m
            continue
        if m.get("words") and hits(text, m["words"]):
            return m
        if m.get("all") and all(hits(text, g) for g in m["all"]):
            return m
    return None


def detect_category(title: str, desc: str, cat: dict) -> str:
    """Категория с наибольшим числом совпадений (слова в названии весят вдвое)."""
    best, best_score = "", 0
    for key, c in cat["categories"].items():
        t, d = title, desc
        for ex in c.get("exclude", []):
            t, d = t.replace(low(ex), " "), d.replace(low(ex), " ")
        score = 2 * len(hits(t, c["words"])) + len(hits(d, c["words"]))
        if score > best_score:
            best, best_score = key, score
    if not best:   # рекомендации и профиль обрезают название на 30 знаках: «…Prada Sport Jacke» → jacket
        m = re.search(r"([a-z]{2,})\W*$", title)
        if m:
            for key, c in cat["categories"].items():
                if any(w.isascii() and w.startswith(m.group(1)) and w != m.group(1) for w in map(low, c["words"])):
                    return key
    return best


NON_FASHION = ["笔记本", "电脑", "镜头", "相机", "手机", "耳机", "平板", "显卡", "主板", "内存", "键盘", "鼠标", "游戏机",
               "显示器", "充电", "电池", "转接", "零食", "桂圆", "茶叶", "白酒", "红酒", "书籍", "玩具", "手办", "乐高", "汽车",
               "电动车", "自行车", "家具", "沙发", "宝马", "奔驰", "奥迪", "改装", "轮毂", "车型", "整车","laptop", "lens", "camera", "ipad", "iphone", "switch", "ps5", "nintendo"]
SOLD_WORDS = ["售出欣赏", "sold欣赏", "已售出", "已出勿拍", "仅供欣赏", "sold 欣赏", "欣赏帖"]

# «复刻/同款» не о реплике: «Prada потом выпускала куртки, повторяющие (复刻) эту модель», «как у звезды (明星同款)»
FAKE_OK_PHRASES = ["来复刻", "复刻这", "复刻了这", "官方复刻", "品牌复刻", "被复刻", "新款复刻", "明星同款", "秀场同款"]


def placeholder_price(p: float) -> bool:
    """¥599999, ¥99999, ¥8888 — цена «для вида»: вещь не продают, а показывают (или зовут в личку)."""
    if p >= 100000:
        return True
    s = str(int(p))
    if p >= 5000 and len(s) >= 4 and (len(set(s)) == 1 or re.fullmatch(r"\d?(?:9{4,}|8{4,}|6{4,})", s)):     # 7999 — живая цена, 19999/58888 — нет
        return True
    return s in ("12345", "123456", "1234567")


def style_fake(text: str, aliases) -> list[str]:
    """«克罗心风格», «prada风», «Chromehearts 风格吊坠» — «в стиле бренда»: это не вещь бренда.
    Общий «复古风格» (ретро-стиль) не трогаем: ловим только бренд сразу перед «风»."""
    t = text.replace(" ", "")
    for al in aliases:
        a_ = low(al).replace(" ", "")
        # «prada风衣» (тренч) и «风琴卡包» (визитница-гармошка) — настоящие вещи, их не трогаем
        if len(a_) >= 2 and re.search(re.escape(a_) + r"(?:家)?风(?![衣琴扇帽镜])", t):
            return [a_ + "风格"]
    return []


def fake_text(text: str) -> str:
    t = text.replace(" ", "")
    for ph in FAKE_OK_PHRASES:
        t = t.replace(ph, "")
    return t


TRUNC_LEN = 32
CAT_FAMILY = {"shoes": "feet", "sneakers": "feet", "outerwear": "wear", "clothing": "wear",
              "bag": "bag", "accessories": "acc", "jewelry": "jew", "eyewear": "eye", "other": "other"}
UNKNOWN_CAT = {"name": "Тип не ясен — откройте карточку", "words": [], "min_price": 500}


TRUST_WORDS = [
    "专柜", "小票", "发票", "防尘袋", "正品", "购买凭证", "包装", "盒子",
    "保真", "可验", "支持验货", "鉴定", "国内专柜", "官网", "购自",
]
DEFECT_WORDS = [
    "破损", "开胶", "掉皮", "磨损严重", "瑕疵", "污渍", "发霉", "异味",
    "破洞", "变形", "掉色", "划痕严重", "脱线",
]
NO_DEFECT_PHRASES = ["无瑕疵", "无明显瑕疵", "没有瑕疵", "无破损", "无污渍", "无异味"]
# Степень износа из текста -> базовая оценка состояния (первое совпадение)
COND_GRADES = [
    (r"几乎全新|近全新|准新", 92), (r"全新|未使用|未拆|吊牌", 98), (r"99新|九九新", 95), (r"9[78]新", 92), (r"9[56]新|九五新", 88),
    (r"9成新|九成新|9[0-4]新", 80), (r"8成新|八成新|8\d新", 65), (r"7成新|七成新|7\d新", 50),
]

# Сигналы для категории B (низкая цена). Это НЕ доказательство подлинности:
# подделки тоже пишут «正品» и «小票». Сигналы только поднимают приоритет ручной проверки.
LOW_STRONG = ["小票", "发票", "购买凭证", "购物凭证", "专柜购", "官网购", "购于专柜", "支持验货",
              "可验货", "支持鉴定", "可鉴定", "接受鉴定", "平台验货", "保卡"]
LOW_MEDIUM = ["闲置", "自用", "搬家", "出国", "断舍离", "清仓", "急出", "不懂", "家人送", "朋友送",
              "防尘袋", "原包装", "盒子", "正品", "保真"]
LOW_RED = ["不保真", "不包真", "不接受鉴定", "不鉴定", "拒鉴定", "拒验", "不包验", "仅供参考",
           "不对比"]
# «без возврата» на Goofish пишут почти все продавцы б/у: не скрываем, а немного снижаем надёжность
LOW_SOFT = ["不退不换", "不退换"]
# Подделки шьют новыми: «винтаж/архив» у дешёвой вещи скорее за подлинность,
# а «новое, не распаковано» при цене ниже порога скорее против.
LOW_ARCHIVE = ["中古", "vintage", "老款", "古着", "千禧", "停产"]
NEW_COND = 95


def low_price_signals(text: str) -> tuple[int, list[str], list[str]]:
    strong = has_any(text, LOW_STRONG)
    medium = has_any(text, LOW_MEDIUM)
    red = has_any(text, LOW_RED)
    score = 2 * len(strong) + len(medium)
    return score, strong + medium, red


def condition_score(text: str) -> tuple[int, str, list[str]]:
    base, note = 70, ""
    for rx, val in COND_GRADES:
        m = re.search(rx, text)
        if m:
            base, note = val, m.group()
            break
    clean = text
    for ph in NO_DEFECT_PHRASES:
        clean = clean.replace(ph, "")
    defects = has_any(clean, DEFECT_WORDS)
    bonus = 5 if has_any(text, NO_DEFECT_PHRASES) else 0
    return clamp(base - 12 * len(defects) + bonus), note, defects


# Подписи для ленты: китайские слова-сигналы по-русски (в данных и поиске остаются как есть)
ZH_RU = {
    "破损": "повреждения", "开胶": "отклеилась подошва", "掉皮": "облезает кожа", "磨损严重": "сильный износ",
    "瑕疵": "есть дефекты", "污渍": "пятна", "发霉": "плесень", "异味": "запах", "破洞": "дыры", "变形": "деформация",
    "掉色": "выцвело", "划痕严重": "сильные царапины", "脱线": "расходятся швы",
    "不保真": "не гарантирует оригинал", "不包真": "не гарантирует оригинал", "不接受鉴定": "против экспертизы",
    "不鉴定": "без экспертизы", "拒鉴定": "против экспертизы", "拒验": "против проверки", "不包验": "без проверки",
    "仅供参考": "«только для справки»", "不对比": "не сравнивать", "不退不换": "без возврата", "不退换": "без возврата",
    "小票": "чек", "发票": "счёт", "购买凭证": "подтверждение покупки", "购物凭证": "подтверждение покупки",
    "专柜购": "куплено в бутике", "官网购": "с офиц. сайта", "购于专柜": "куплено в бутике", "支持验货": "можно проверить",
    "可验货": "можно проверить", "支持鉴定": "можно на экспертизу", "可鉴定": "можно на экспертизу",
    "接受鉴定": "можно на экспертизу", "平台验货": "проверка площадкой", "保卡": "гарантийная карта",
    "闲置": "не нужна", "自用": "носил сам", "搬家": "переезд", "出国": "уезжает", "断舍离": "расхламление",
    "清仓": "распродажа", "急出": "срочно", "不懂": "не разбирается", "家人送": "подарок семьи", "朋友送": "подарок друга",
    "防尘袋": "пыльник", "原包装": "родная упаковка", "盒子": "коробка", "正品": "«оригинал»", "保真": "гарантия оригинала",
    "中古": "винтаж", "vintage": "винтаж", "老款": "старая модель", "古着": "винтаж", "千禧": "нулевые",
    "停产": "снято с производства", "专柜": "бутик", "包装": "упаковка", "可验": "можно проверить", "鉴定": "экспертиза",
    "国内专柜": "бутик в Китае", "官网": "офиц. сайт", "购自": "куплено в",
    "原厂": "«с фабрики»", "细节对比": "«сравните детали»", "支持对比": "«можно сравнить»", "专柜品质": "«качество бутика»",
    "品质": "«качество»", "做工精细": "«тонкая работа»", "做工精致": "«тонкая работа»", "高品质": "«высокое качество»",
    "代工厂": "«фабрика-подрядчик»", "工厂店": "«фабричный магазин»", "进口面料": "«импортная ткань»",
    "进口五金": "«импортная фурнитура»", "细节图": "«фото деталей»", "现货": "«в наличии»", "多色": "«много цветов»",
    "美国制造": "США", "意大利制造": "Италия", "日本制造": "Япония", "法国制造": "Франция",
    "made in usa": "США", "made in italy": "Италия", "made in japan": "Япония", "made in france": "Франция",
    "made in england": "Англия", "改小": "уменьшена", "改造": "переделка", "改款": "переделка", "大师改": "переделка мастером",
    "二次改": "переделка", "拼接改": "перешита",
    "验货宝": "验货宝", "专柜小票": "чек бутика", "鉴定证书": "сертификат экспертизы", "鉴定报告": "отчёт экспертизы",
}
_ZH_NUM = {"九": 9, "八": 8, "七": 7}


def ru_words(words) -> str:
    return ", ".join(dict.fromkeys(ZH_RU.get(low(w), w) for w in words))


def cond_ru(note: str) -> str:
    """«99新» → «состояние 99%», «9成新» → «90%», «全新/吊牌» → «новое»."""
    if not note:
        return ""
    if note in ("全新", "未使用", "未拆"):
        return "новое"
    if note == "吊牌":
        return "новое, с бирками"
    if note in ("几乎全新", "近全新", "准新"):
        return "почти новое"
    m = re.match(r"(\d)(\d)新", note)
    if m:
        return f"состояние {m.group(1)}{m.group(2)}%"
    m = re.match(r"(\d|[九八七])成新", note)
    if m:
        return f"состояние {_ZH_NUM.get(m.group(1), m.group(1))}0%"
    return {"九九新": "состояние 99%", "九五新": "состояние 95%"}.get(note, note)


SHOE_CATS, WEAR_CATS =("shoes", "sneakers"), ("clothing", "outerwear")
_SHOE_RX = re.compile(r"(?:eu|尺码|码数|鞋码|size)\s*[:：]?\s*(3[5-9]|4[0-8])(?:\.5)?|(3[5-9]|4[0-8])(?:\.5)?\s*(?:码|号|eu)")
_WEAR_RX = re.compile(r"(?:尺码|码数|size)\s*[:：]?\s*(xxs|xs|xxxl|3xl|xxl|2xl|xl|s|m|l)(?![a-z])"
                      r"|(?<![a-z])(xxs|xs|xxxl|3xl|xxl|2xl|xl|s|m|l)\s*(?:码|号)"
                      r"|(?:尺码|码数|size|意码|it)\s*[:：]?\s*(4[2-9]|5[0-8])(?!\d)|(?<!\d)(4[4-9]|5[0-6])\s*(?:码|号)")
# Moncler нумерует размеры одежды цифрой: 0 = XS, 1 = S, 2 = M, 3 = L, 4 = XL, 5 = XXL, 6–7 = 3XL («3码», «サイズ2»)
_MONCLER_RX = re.compile(r"(?:尺码|码数|size|サイズ)\s*[:：]?\s*([0-7])(?![\d.a-z码])|(?<![\d.])([0-7])\s*(?:码|号)|(?:尺码|码数)\s*[:：]?\s*([0-7])\s*码")
_MONCLER_SIZE = ["xs", "s", "m", "l", "xl", "xxl", "xxxl", "xxxl"]
WEAR_BONUS = {"m": 6, "l": 6, "xl": 2, "s": 2, "xxl": -3, "2xl": -3, "xs": -10, "xxs": -12, "xxxl": -10, "3xl": -10}


def size_liquidity(text: str, ckey: str, brand: str = "") -> tuple[str, int]:
    """Размер из объявления и поправка ликвидности: ходовые мужские размеры продаются быстрее."""
    if ckey in SHOE_CATS:
        m = _SHOE_RX.search(text)
        if m:
            n = int(m.group(1) or m.group(2))
            return f"{n}", 8 if 41 <= n <= 44 else 2 if n in (40, 45) else -6 if n in (39, 46) else -15
    elif ckey in WEAR_CATS:
        m = _WEAR_RX.search(text)
        if m:
            v = next(g for g in m.groups() if g)
            if v.isdigit():
                n = int(v)
                return f"{n} (IT)", 6 if 46 <= n <= 50 else 1 if n in (44, 52) else -8
            return v.upper(), WEAR_BONUS.get(v, 0)
        m = _MONCLER_RX.search(text) if brand == "Moncler" else None
        if m:
            v = _MONCLER_SIZE[int(next(g for g in m.groups() if g))]
            return v.upper(), WEAR_BONUS.get(v, 0)
    return "", 0


def trust_score(text: str, tier_b: bool) -> tuple[int, list[str], list[str]]:
    """Надёжность (чем выше, тем меньше риск подделки)."""
    trust = has_any(text, TRUST_WORDS)
    strong = has_any(text, LOW_STRONG)
    red = has_any(text, LOW_RED)
    soft = has_any(text, LOW_SOFT)[:1]
    s = 55 + 6 * min(len(trust), 4) + 8 * min(len(strong), 2) - 35 * len(red) - 10 * len(soft) - (15 if tier_b else 0)
    return clamp(s), trust, red + soft


# Комплект у обуви и сумок: коробка + пыльник поднимают цену продажи, «без коробки» — снижает (±10%)
KIT_CATS = ("sneakers", "shoes", "bag")
KIT_NONE = ["无盒", "没盒", "没有盒", "无鞋盒", "不带盒", "无原盒", "裸鞋", "无配件", "仅鞋", "只有鞋", "无防尘袋", "没有防尘袋"]
KIT_FULL = ["全套", "配件齐全", "包装齐全", "全配"]
KIT_BOX, KIT_BAG = ["鞋盒", "盒子", "原盒", "包装盒"], ["防尘袋", "尘袋", "鞋袋"]


def kit_factor(text: str, ckey: str) -> tuple[float, str]:
    if ckey not in KIT_CATS:
        return 1.0, ""
    if has_any(text, KIT_NONE):
        return 0.9, "без коробки"
    if has_any(text, KIT_FULL) or (has_any(text, KIT_BOX) and has_any(text, KIT_BAG)):
        return 1.1, "коробка и пыльник"
    return 1.0, ""


def realistic_sell(sell: float, price_cny: float, cond: int, rate: float, cap_k: float | None,
                   kit: tuple[float, str] = (1.0, "")) -> tuple[int, str]:
    """Цена продажи в РФ и пояснение, если она изменена. Цена из каталога — для типичной вещи в хорошем
    состоянии; вещь в разы дешевле рынка часто «не та» (меньше, старее, убитая), поэтому снижаем,
    но не ниже половины цены каталога. cap_k=None — ваша собственная цена из prices.json, её не трогаем."""
    if cap_k is None:
        return round(sell), ""
    base, notes = sell, []
    if kit[0] != 1.0:
        sell *= kit[0]
        notes.append(kit[1])
    if cond < 80:
        sell *= 0.6 + 0.4 * cond / 100        # состояние 50% → ×0.8
        notes.append("за состояние")
    cap = max(price_cny * rate * cap_k, base * 0.5, 3000)
    if sell > cap:
        sell = cap
        notes.append("вещь сильно дешевле рынка")
    sell = round(sell / 500) * 500
    if sell > base * 1.05:
        return sell, f"выше {round(base):,} ₽: ".replace(",", " ") + ", ".join(notes)
    return sell, (f"снижено с {round(base):,} ₽: ".replace(",", " ") + ", ".join(notes)) if sell < base * 0.95 and notes else ""



# ---------------------------------------------------------------- Mercari (Япония, jp.mercari.com)
# Ответы страницы Mercari, которую вы (или автопоиск) открыли: выдача api.mercari.jp/v2/entities:search, карточка
# items/get, вещи продавца items/get_items. Цена — в иенах: в базе price_cny = иены (ради сравнения цен), в ленте
# load_rows пересчитывает её в юань-эквивалент по курсам; price_jpy — для показа.
MERCARI_MARKERS = ("api.mercari.jp/v2/entities:search", "api.mercari.jp/items/get")
# японские слова -> слова каталога (тип вещи, состояние): дописываются в метки, чтобы их узнали те же правила
JP_WORDS = [
    ("マウンテンパーカー", "jacket"), ("レザージャケット", "皮衣"), ("ライダース", "皮衣"), ("ダウンジャケット", "down jacket"),
    ("ダウンベスト", "马甲"), ("ジャケット", "jacket"), ("ブルゾン", "jacket"), ("アウター", "jacket"), ("コート", "coat"),
    ("ダウン", "羽绒"), ("ベスト", "马甲"), ("ジレ", "马甲"),
    ("tシャツ", "t恤"), ("カットソー", "t恤"), ("シャツ", "shirt"), ("スウェット", "卫衣"), ("パーカー", "hoodie"),
    ("フーディ", "hoodie"), ("ニット", "针织"), ("セーター", "sweater"), ("カーディガン", "针织"), ("パンツ", "裤"),
    ("スラックス", "裤"), ("ショーツ", "裤"), ("デニム", "牛仔"), ("ジーンズ", "牛仔"),
    ("スニーカー", "sneaker"), ("ブーツ", "boot"), ("ローファー", "loafer"), ("革靴", "皮鞋"), ("ドレスシューズ", "皮鞋"),
    ("サンダル", "凉鞋"), ("シューズ", "鞋"),
    ("リュック", "backpack"), ("バックパック", "backpack"), ("トートバッグ", "tote"), ("ショルダーバッグ", "crossbody"),
    ("メッセンジャー", "messenger"), ("ボストン", "bag"), ("バッグ", "bag"),
    ("長財布", "wallet"), ("財布", "wallet"), ("ウォレット", "wallet"), ("キーケース", "钥匙包"), ("ベルト", "皮带"),
    ("キャップ", "帽"), ("ハット", "帽"), ("ニット帽", "帽"), ("マフラー", "围巾"), ("ストール", "围巾"), ("手袋", "手套"),
    ("指輪", "ring"), ("リング", "ring"), ("ネックレス", "necklace"), ("ブレスレット", "bracelet"), ("ペンダント", "pendant"),
    ("ピアス", "耳钉"), ("サングラス", "sunglasses"), ("メガネ", "glasses"), ("眼鏡", "glasses"),
    ("腕時計", "watch"), ("時計", "watch"), ("香水", "perfume"),
    ("ヴィンテージ", "vintage"), ("アーカイブ", "archive"), ("古着", "vintage"),
]
# бренды катаканой -> латиница каталога (в выдаче бренд часто есть и полем itemBrand)
JP_BRANDS = {
    "キャピタル": "kapital", "ヴィスヴィム": "visvim", "ビズビム": "visvim", "ナンバーナイン": "number nine",
    "アンダーカバー": "undercover", "ヨウジヤマモト": "yohji yamamoto", "コムデギャルソン": "comme des garcons",
    "ジュンヤワタナベ": "junya watanabe", "イッセイミヤケ": "issey miyake", "サカイ": "sacai", "ネイバーフッド": "neighborhood",
    "ダブルタップス": "wtaps", "ニードルズ": "needles", "ヒステリックグラマー": "hysteric glamour", "マスターマインド": "mastermind",
    "プラダ": "prada", "マルジェラ": "maison margiela", "ゴールデングース": "golden goose", "ボッテガ": "bottega veneta",
    "リックオウエンス": "rick owens", "バレンシアガ": "balenciaga", "ストーンアイランド": "stone island",
    "モンクレール": "moncler", "クロムハーツ": "chrome hearts", "ルイヴィトン": "louis vuitton", "ヴィトン": "louis vuitton",
    "バーバリー": "burberry", "ラフシモンズ": "raf simons", "ヘルムートラング": "helmut lang",
    "アンドゥムルメステール": "ann demeulemeester", "グイディ": "guidi", "ユリウス": "julius", "クレイググリーン": "craig green",
    "ミハラヤスヒロ": "maison mihara yasuhiro", "ゴルチエ": "jean paul gaultier", "ヴィヴィアン": "vivienne westwood",
    "ジルサンダー": "jil sander", "ルメール": "lemaire", "アクネ": "acne studios", "ドリスヴァンノッテン": "dries van noten",
    "エルメス": "hermes", "グッチ": "gucci", "セリーヌ": "celine", "ロエベ": "loewe", "サンローラン": "saint laurent",
    "ディオール": "dior", "フェンディ": "fendi", "ジバンシィ": "givenchy", "ヴァレンティノ": "valentino", "ヴェルサーチ": "versace",
    "ゴヤール": "goyard", "ベルルッティ": "berluti", "ロロピアーナ": "loro piana", "トムブラウン": "thom browne",
    "ジョンロブ": "john lobb", "パラブーツ": "paraboot", "シュプリーム": "supreme", "ラルフローレン": "ralph lauren",
    "ディーゼル": "diesel", "キャロルクリスチャンポエル": "carol christian poell", "ボリスビジャンサベリ": "boris bidjan saberi",
    "シーピーカンパニー": "c.p. company", "アークテリクス": "arc'teryx", "バブアー": "barbour", "ベルスタッフ": "belstaff",
    "オフホワイト": "off-white", "ワイスリー": "y-3",
}
# состояние Mercari (itemConditionId) -> слова, которые понимает condition_score
JP_COND = {"1": "全新", "2": "几乎全新", "3": "95新", "4": "9成新", "5": "8成新", "6": "7成新"}
JP_COND_RU = {"1": "новое", "2": "почти новое", "3": "без заметных следов", "4": "небольшие следы", "5": "следы износа",
              "6": "плохое состояние"}


def jp_labels(name: str, brand: str = "", cond: str = "", size: str = "") -> str:
    """Метки для японской вещи: бренд латиницей, тип и состояние словами каталога, размер (обувь — см -> EU)."""
    t = name.lower().replace("ｔ", "t")
    out = [f"品牌: {brand}"] if brand else []
    out += sorted({v for k, v in JP_BRANDS.items() if k in name and v.lower() not in brand.lower()})
    seen = set()
    for k, v in JP_WORDS:
        if k in t and v not in seen:
            seen.add(v)
            out.append(v)
    if cond in JP_COND:
        out.append(JP_COND[cond])
    m = re.match(r"(2[2-9](?:\.5)?)\s*cm", size or "")
    if m:       # японский размер обуви в см -> EU (26 см ≈ 41, 27 ≈ 42–43, 28 ≈ 44)
        out.append(f"尺码: {int(round(float(m.group(1)) * 1.5 + 2))}")
    elif size and re.fullmatch(r"(?i)xxs|xs|s|m|l|xl|xxl|xxxl", size.strip()):
        out.append(f"尺码: {size.strip().lower()}")
    return " | ".join(out)


def mercari_row(it: dict) -> dict | None:
    """Вещь из выдачи Mercari (camelCase) или из списка вещей продавца / карточки (snake_case)."""
    iid = str(it.get("id") or "")
    price = to_float(it.get("price"))
    if not iid or not price:
        return None
    brand = it.get("itemBrand") or it.get("item_brand") or {}
    size = it.get("itemSize") or it.get("item_size") or {}
    cond = str(it.get("itemConditionId") or (it.get("item_condition") or {}).get("id") or "")
    thumbs = it.get("thumbnails") or []
    shop = it.get("itemType") == "ITEM_TYPE_BEYOND" or bool(it.get("shop")) or not iid.startswith("m")
    created = str(it.get("created") or "")
    payer = str(it.get("shippingPayerId") or (it.get("shipping_payer") or {}).get("id") or "")
    status = str(it.get("status") or "")
    return {
        "id": iid, "site": "mercari",
        "title": str(it.get("name") or "")[:200],
        "price_cny": price, "price_jpy": price,        # в базе — иены; в юань-эквивалент переводит load_rows
        "city": "Япония",
        "seller": (it.get("shopName") or "") if shop else "",
        "seller_id": "" if str(it.get("sellerId") or "") == "0" else      # у Mercari Shops продавец «0» — не id
                     str(it.get("sellerId") or (it.get("seller") or {}).get("id") or ""),
        "want": int(to_float(it.get("num_likes") or 0)),
        "posted": datetime.fromtimestamp(int(created)).isoformat(timespec="seconds") if created.isdigit() else "",
        "free_ship": payer == "2",      # 送料込み — доставка по Японии до посредника уже в цене
        "tags": "Mercari Shops" if shop else "",
        "image": (thumbs[0] if thumbs else "") or "",
        "url": f"https://jp.mercari.com/{'shops/product' if shop else 'item'}/{iid}",
        "desc": "",
        "labels": jp_labels(str(it.get("name") or ""), str(brand.get("name") or ""), cond, str(size.get("name") or "")),
        "jp_cond": JP_COND_RU.get(cond, ""),
        "queries": "",
        "sold": "SOLD" in status.upper() or "TRADING" in status.upper(),
    }


def parse_mercari(api_url: str, data: dict) -> tuple[str, list[dict], dict | None, dict]:
    """-> (вид ответа: search / detail / seller, вещи, продавец (для карточки), условие поиска)."""
    if "entities:search" in api_url:
        # проданные (売り切れ) тоже отдаём: сервер пишет их в таблицу sold — это цены настоящих сделок
        rows = [r for r in (safe_parse(mercari_row, x, "Mercari, выдача") for x in data.get("items") or []) if r]
        return "search", rows, None, data.get("searchCondition") or {}
    d = data.get("data")
    if "items/get_items" in api_url and isinstance(d, list):
        return "seller", [r for r in (safe_parse(mercari_row, x, "Mercari, продавец") for x in d) if r and not r["sold"]], None, {}
    if isinstance(d, dict) and d.get("id"):
        row = mercari_row(d)
        sel = d.get("seller") or {}
        seller = None
        if sel.get("id"):
            rt = sel.get("ratings") or {}
            created = to_float(sel.get("created"))
            seller = {"uid": str(sel["id"]), "nick": str(sel.get("name") or "")[:40],
                      "days": int((datetime.now().timestamp() - created) / 86400) if created else 0,
                      "good": int(rt.get("good") or 0), "bad": int(rt.get("bad") or 0),
                      "sold": int(sel.get("num_sell_items") or 0), "visit": "", "site": "mercari"}
        if row:
            cat = d.get("item_category") or {}
            row["desc"] = (str(d.get("description") or "").replace("\n", " ")[:700]
                           + (f" 【{cat.get('parent_category_name', '')} {cat.get('name', '')}】" if cat else ""))
            row["want"] = int(d.get("num_likes") or 0)
            if seller:
                row["seller"] = seller["nick"]
        return "detail", [row] if row else [], seller, {}
    return "", [], None, {}


__all__ = [n for n in dir() if not n.startswith("__")]   # с «_именами»: модули ниже берут их через *
