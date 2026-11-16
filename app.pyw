"""Sheben'Store (Goofish Грааль): одно окно со вкладками Goofish / Лента / Каталог / Цены / Статус.

Двойной щелчок (или ярлык на рабочем столе):
  - запускает приём объявлений (то же, что goofish_parser_pro.py --listen);
  - открывает окно: Goofish во встроенном браузере (движок Edge WebView2) и вкладки приложения;
  - кнопка «Рядом» показывает Goofish и ленту бок о бок.
Во вкладке Goofish работает тот же перехват, что в расширении chrome_ext/hook.js: читаются только
ответы поиска, которые страница получила, пока вы листаете. Сам на Goofish ничего не запрашивает.
Вход в Goofish делаете вы; он хранится в папке app_data рядом со скриптом.
Закрытие окна останавливает приём, всё собранное остаётся в базе.

Запасной режим без встроенного браузера: app.pyw --chrome (два окна Chrome рядом и значок в трее).
"""
import ctypes
import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
os.chdir(HERE)
(HERE / "out").mkdir(exist_ok=True)
sys.path.insert(0, str(HERE))
import config  # noqa: E402  — паузы автопоиска и журнал: значения по умолчанию в config.py, свои — в config.json
import gp_log  # noqa: E402
import gp_throttle  # noqa: E402
from gp_log import say  # noqa: E402
# у pythonw нет консоли: всё, что печатают парсер и библиотеки, идёт в журнал logs/sheben.log (с датой, уровнем и ротацией)
gp_log.redirect_std()
LOG = gp_log.log_dir() / "sheben.log"

FEED = "http://127.0.0.1:8765/"
GOOFISH = "https://www.goofish.com/"
MERCARI = "https://jp.mercari.com/"
DATA_DIR = HERE / "app_data"
CHROME = [Path(os.environ.get(v, "")) / "Google/Chrome/Application/chrome.exe"
          for v in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]


def server_alive() -> bool:
    try:
        with urllib.request.urlopen(FEED + "version", timeout=1) as r:
            return r.status == 200
    except Exception:
        return False


def server_info(path: str = "version", timeout: float = 1) -> dict:
    try:
        with urllib.request.urlopen(FEED + path, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return {}


def run_server():
    import goofish_parser_pro as gp
    try:
        gp.main(["--listen"])
    except SystemExit as e:
        say("Парсер остановился:", e)
    except Exception as e:
        say("Ошибка парсера:", repr(e))


def start_server() -> bool:
    """Запускает приём, если он ещё не запущен (например, в терминале). True, если запустили мы."""
    if server_alive():
        return False
    say(f"\n=== запуск приложения {time.strftime('%Y-%m-%d %H:%M')} ===")
    threading.Thread(target=run_server, daemon=True).start()
    for _ in range(200):        # сервер отвечает сразу после загрузки модулей; лента досчитывается в фоне под заставкой
        if server_alive():
            break
        threading.Event().wait(0.1)
    return True


def send_items(raw: str) -> dict:
    """Ответ поиска со вкладки Goofish -> сервер (тот же путь, что у расширения). Вернёт итог разбора:
    запрос, сколько объявлений, сколько новых и сколько известных давно — по нему автопоиск решает, листать ли дальше."""
    req = urllib.request.Request(FEED + "items", data=raw.encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "X-Goofish-Ext": "1"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:      # вызывается из фоновой нити — окно не ждёт
            return json.loads(r.read() or b"{}")
    except Exception as e:
        # сервер занят (пересчёт ленты) — ответ не теряем: в очередь, повторим в фоне
        RETRY.append(raw)
        say(f"  сервер не принял ответ поиска ({type(e).__name__}) — в очереди повтора: {len(RETRY)}")
        _retry_start()
        return {"queued": True}


RETRY = __import__("collections").deque(maxlen=300)      # ответы поиска, которые сервер не принял
RETRY_DONE: list = []       # кому сообщить об удачном повторе (автопоиск дописывает строку в свой журнал)
_retry_on = [False]


def _retry_start():
    if not _retry_on[0]:
        _retry_on[0] = True
        threading.Thread(target=_retry_bg, daemon=True).start()


def _retry_bg():
    """Повторы по одному, с растущей паузой 3 → 60 с: сервер отвечает снова, как только закончит пересчёт.
    Разбор на сервере повтор переносит спокойно — та же вещь второй раз «новой» не считается."""
    wait = 3
    while True:
        time.sleep(wait)
        if not RETRY:
            wait = 3
            continue
        raw = RETRY[0]
        req = urllib.request.Request(FEED + "items", data=raw.encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/json", "X-Goofish-Ext": "1"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                res = json.loads(r.read() or b"{}")
        except Exception as e:
            wait = min(wait * 2, 60)
            say(f"  повтор не прошёл ({type(e).__name__}), следующий через {wait} с; в очереди {len(RETRY)}")
            continue
        try:
            RETRY.popleft()
        except IndexError:
            pass
        wait = 1 if RETRY else 3
        say(f"  ответ поиска передан повтором: «{res.get('q', '')}», объявлений {res.get('n', 0)}, новых {res.get('added', 0)}; "
              f"в очереди осталось {len(RETRY)}")
        for cb in list(RETRY_DONE):
            try:
                cb(res)
            except Exception:
                pass


def post_json(path: str, body: dict):
    """Служебные данные приложения -> сервер (тот же заголовок, что у ответов поиска)."""
    req = urllib.request.Request(FEED + path, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "X-Goofish-Ext": "1"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=15).read() or b"{}")
    except Exception as e:
        say(f"  не удалось передать {path}:", repr(e)[:120])
        return {}


def send_from_goofish_tab(raw: str):
    """Сообщение со вкладки Goofish: ответ поиска Goofish, карточки Oskelly / Second Friend
    или кнопка «Следить за продавцом» с панели на странице товара."""
    if raw.startswith('{"market"'):
        try:
            post_json("market/items", json.loads(raw))
        except ValueError:
            pass
    elif raw.startswith('{"unhide"'):    # «Вернуть в ленту» на панели скрытой вещи
        try:
            return post_json("mark/unhide", {"id": str(json.loads(raw)["unhide"])})
        except (ValueError, KeyError, TypeError):
            return {}
    elif raw.startswith('{"follow"'):
        try:
            f = json.loads(raw)["follow"]
            uid = str(f["uid"])
        except (ValueError, KeyError, TypeError):
            return {}
        body = {"uid": uid, "nick": str(f.get("nick", "")), "on": bool(f.get("on", True)), "site": str(f.get("site", ""))}
        if "every" in f:        # «каждый круг» / «раз в 3 круга»
            body["every"] = bool(f["every"])
        res = post_json("sellers/follow", body)
        return {"follow": {"uid": uid, "followed": bool(res.get("followed")), "every": bool(res.get("every"))}} \
            if res.get("ok") else {}
    else:
        return send_items(raw)
    return {}


def tg_message(x: dict) -> dict:
    """Уведомление -> сообщение Telegram (HTML) и фото вещи."""
    from html import escape
    fmt = lambda v: f"{v:,.0f}".replace(",", " ")
    if x.get("kind") == "test":
        return {"text": escape(x.get("text", ""))}
    if x.get("kind") == "stop":
        return {"text": "⏸ " + escape(x.get("text", ""))}
    if x.get("kind") == "info":
        return {"text": "ℹ️ " + escape(x.get("text", ""))}
    if x.get("kind") == "summary":
        return {"text": "<b>☀️ Сводка</b>\n" + escape(x.get("text", ""))}
    head = {"new": "🆕 Новая выгодная вещь", "drop": "📉 Цена снижена", "relist": "♻️ Перевыставлено дешевле",
            "order": f"🧾 Под заказ: {escape(x.get('text', ''))}"}.get(x.get("kind"), "Уведомление")
    p = x.get("profit_rub")
    price = x.get("price_txt") or (f"¥{fmt(x['price_cny'])}" + (f" (было ¥{fmt(x['old_price'])})" if x.get("old_price") else ""))
    prof = f"\nприбыль {'+' if p > 0 else ''}{fmt(p)} ₽" if isinstance(p, (int, float)) else ""
    img = x.get("image") or ""
    img = ("https:" + img if img.startswith("//") else img).replace("http:", "https:")
    return {"text": f"<b>{head}</b>\n{escape(x.get('title', ''))}\n{price}{prof}"
                    + (f"\nвыложено {escape(x['age'])} назад" if x.get("age") else "")
                    + (f"\nзапрос «{escape(x['q'])}»" if x.get("q") else "")
                    + (f"\n<a href=\"{escape(x['url'])}\">Открыть на {'Mercari' if x.get('site') == 'mercari' else 'Goofish'}</a>"
                       if x.get("url") else ""),
            "photo": img if img.startswith("https://") else ""}


# публичная веб-версия канала: посты, ссылки, даты; ответ — в приложение
TG_EXTRACT = """(()=>{const ms=[...document.querySelectorAll('.tgme_widget_message[data-post]')];
const items=ms.map(m=>{const t=m.querySelector('.tgme_widget_message_text'),d=m.querySelector('.tgme_widget_message_date time');
 return {id:m.dataset.post,url:'https://t.me/'+m.dataset.post,date:d?d.getAttribute('datetime'):'',text:t?t.innerText:''}}).filter(x=>x.text);
const ids=ms.map(m=>+m.dataset.post.split('/')[1]).filter(Boolean);
window.chrome.webview.postMessage(JSON.stringify({market:'__SRC__',items,oldest:ids.length?Math.min(...ids):null}))})()"""
SHOP_HOSTS = ("oskelly.ru", "secondfriendstore.ru")
# карточки товаров на странице площадки: текст карточки (бренд, название, цена) и ссылка
SHOP_EXTRACT = """(()=>{const seen=new Set(),items=[];for(const a of document.querySelectorAll('a[href*="/products/"]')){
 const href=a.href.split('?')[0];if(seen.has(href))continue;let el=a,txt=a.innerText||'';
 for(let i=0;i<4&&!/₽|руб/.test(txt)&&el.parentElement;i++){el=el.parentElement;txt=el.innerText||''}
 if(!/₽|руб/.test(txt)||txt.length>500)continue;seen.add(href);items.push({id:href,url:href,date:'',text:txt})}
 if(items.length&&JSON.stringify(items)!==window.__shopLast){window.__shopLast=JSON.stringify(items);
  window.chrome.webview.postMessage(JSON.stringify({market:'shop:'+location.hostname,items}))}})()"""
# Avito: объявления со страницы, которую вы сами открыли во вкладке Avito (поиск или само объявление). Ничего не нажимаем
# и не листаем — только текст карточки (название, цена) и ссылка. Ссылка на объявление кончается на _<номер>.
AVITO_EXTRACT = r"""(()=>{const seen=new Set(),items=[],isItem=h=>/_\d{7,}$/.test(h);
 for(const a of document.querySelectorAll('a[href]')){const href=a.href.split('?')[0].split('#')[0];
  if(!/avito\.ru\//.test(href)||!isItem(href)||seen.has(href))continue;let el=a.closest('[data-marker="item"]'),txt=el?el.innerText||'':'';
  // карточка без разметки: поднимаемся к блоку с ценой, но не выше карточки (в блоке — ссылки только на это объявление)
  if(!el){el=a;for(let i=0;i<6&&el.parentElement;i++){const up=el.parentElement;
   if(new Set([...up.querySelectorAll('a[href]')].map(x=>x.href.split('?')[0].split('#')[0]).filter(isItem)).size>1)break;
   el=up;txt=el.innerText||'';if(/₽/.test(txt))break}}
  if(!/₽/.test(txt)||txt.length>700)continue;seen.add(href);items.push({id:href,url:href,date:'',text:txt.replace(/\s+/g,' ').trim()})}
 const here=location.href.split('?')[0].split('#')[0],h1=document.querySelector('h1');
 if(isItem(here)&&h1&&!seen.has(here)){const pr=document.querySelector('[itemprop="price"]');
  const p=pr?(pr.getAttribute('content')||pr.innerText):'';if(p)items.push({id:here,url:here,date:'',text:h1.innerText+' '+p+' ₽'})}
 if(items.length&&JSON.stringify(items)!==window.__avLast){window.__avLast=JSON.stringify(items);
  window.chrome.webview.postMessage(JSON.stringify({market:'shop:avito',items}))}})()"""
# отправка в Telegram со страницы api.telegram.org; chat_id находится сам по последнему /start
TG_SEND = """(async()=>{const T=__TOKEN__,P=__PAYLOAD__;let chat=__CHAT__;const out={};
const api=(m,b)=>fetch('/bot'+T+'/'+m,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b||{})}).then(r=>r.json());
try{if(!chat){const u=await api('getUpdates',{});if(!u.ok)throw u.description||'getUpdates';
  const ms=(u.result||[]).map(x=>x.message).filter(Boolean);if(!ms.length)throw 'напишите своему боту /start';
  chat=ms[ms.length-1].chat.id;out.chat_id=chat}
 for(const x of P){let r=x.photo?await api('sendPhoto',{chat_id:chat,photo:x.photo,caption:x.text,parse_mode:'HTML'}):{ok:false};
  if(!r.ok)r=await api('sendMessage',{chat_id:chat,text:x.text,parse_mode:'HTML'});if(!r.ok)throw r.description||'sendMessage'}
 out.ok=1}catch(e){out.error=String(e)}window.chrome.webview.postMessage(JSON.stringify(out))})()"""


def hook_script() -> str:
    js = (HERE / "chrome_ext" / "hook.js").read_text(encoding="utf-8")
    old = "window.postMessage({ __goofishParser: true, apiUrl: String(url), pageUrl: location.href, data: data }, location.origin);"
    assert old in js, "hook.js изменился: обновите hook_script() в app.pyw"
    new = ("if (window.chrome && window.chrome.webview) window.chrome.webview.postMessage("
           "JSON.stringify({ apiUrl: String(url), pageUrl: location.href, data: data }));")
    return js.replace(old, new)


# ---------------------------------------------------------------- одно окно (WebView2)
def run_window(own_server: bool):
    import clr
    lib = Path(importlib.util.find_spec("webview").origin).parent / "lib"
    clr.AddReference("System.Windows.Forms")
    clr.AddReference("System.Drawing")
    clr.AddReference(str(lib / "Microsoft.Web.WebView2.Core.dll"))
    clr.AddReference(str(lib / "Microsoft.Web.WebView2.WinForms.dll"))
    from System import Action
    from System.Drawing import Color, Font, FontStyle, Icon, Point, Rectangle, Size, SolidBrush, SystemIcons
    from System.Drawing.Drawing2D import GraphicsPath, SmoothingMode
    from System.Reflection import BindingFlags
    from System.Threading import ApartmentState, Thread, ThreadStart
    from System.Windows.Forms import (Application, Cursors, DockStyle, Form, FormWindowState, Label, NotifyIcon, Padding,
                                      Panel, SplitContainer, TextFormatFlags, TextRenderer, Timer, ToolTipIcon,
                                      ContextMenuStrip, ToolStripMenuItem)
    from urllib.parse import quote
    from Microsoft.Web.WebView2.WinForms import CoreWebView2CreationProperties, WebView2

    hook = hook_script()
    card_panel = (HERE / "ui" / "card_panel.js").read_text(encoding="utf-8")
    bar_bg, fg, accent, accent_fg = (Color.FromArgb(21, 26, 24), Color.FromArgb(231, 235, 232),
                                     Color.FromArgb(134, 196, 174), Color.FromArgb(13, 18, 16))
    muted, line, hover_bg, gold = (Color.FromArgb(146, 154, 149), Color.FromArgb(42, 49, 45),
                                   Color.FromArgb(35, 42, 39), Color.FromArgb(226, 189, 85))
    ui = {"mode": "goofish", "route": ""}

    def ui_thread():
        form = Form()
        form.Text = "Sheben'Store"
        form.WindowState = FormWindowState.Maximized
        form.BackColor = bar_bg
        if (HERE / "app.ico").exists():
            form.Icon = Icon(str(HERE / "app.ico"))

        split = SplitContainer()
        split.Dock = DockStyle.Fill
        split.SplitterWidth = 6
        split.BackColor = bar_bg

        def make_view():
            props = CoreWebView2CreationProperties()
            props.UserDataFolder = str(DATA_DIR)
            v = WebView2()
            v.CreationProperties = props
            v.Dock = DockStyle.Fill
            return v

        gv, av = make_view(), make_view()
        split.Panel1.Controls.Add(gv)
        split.Panel2.Controls.Add(av)

        # --- панель вкладок: рисуется вручную (логотип, вкладки-«таблетки» с иконками, счётчик новых)
        bar = Panel()
        bar.Dock = DockStyle.Top
        bar.Height = 54
        bar.BackColor = bar_bg
        bar.GetType().GetProperty("DoubleBuffered", BindingFlags.NonPublic | BindingFlags.Instance).SetValue(bar, True, None)
        f_tab, f_icon = Font("Segoe UI Semibold", 10), Font("Segoe MDL2 Assets", 10)
        f_logo, f_s, f_badge = Font("Segoe UI", 13, FontStyle.Bold), Font("Segoe UI", 12, FontStyle.Bold), Font("Segoe UI", 8, FontStyle.Bold)
        items, bar_state = [], {"hover": None, "badge": 0}
        TF = TextFormatFlags

        def button(key, text, icon, handler, side="l"):
            items.append({"key": key, "text": text, "icon": icon, "handler": handler, "side": side, "rect": None})

        def group(key, text, icon, members, side="l"):
            """Вкладка-группа (Площадки / Рынок России / Ещё): нажатие — последняя открытая страница группы,
            а если вы уже в группе (или нажали «▾») — меню её страниц. members: [(режим, подпись, действие)]."""
            items.append({"key": key, "text": text, "icon": icon, "members": members, "last": members[0][0],
                          "handler": None, "side": side, "rect": None})

        def label(it):
            """Подпись вкладки: у группы — где вы сейчас («Mercari ▾»), иначе название группы."""
            if not it.get("members"):
                return it["text"]
            cur = next((t for k, t, _ in it["members"] if k == ui["mode"]), None)
            return (cur or it["text"]) + "  ▾"

        def in_group(it):
            return any(k == ui["mode"] for k, _, _ in it.get("members") or [])

        # своё меню группы: тёмное, со скруглением, активная страница — как активная вкладка панели.
        # (Системное белое меню Windows выбивалось из вида; перекрасить его целиком нельзя.)
        from System.Drawing import Pen, Region
        from System.Windows.Forms import FormBorderStyle, FormStartPosition, Keys
        MENU_ROW, MENU_PAD = 36, 6
        pop = Form()
        pop.FormBorderStyle = getattr(FormBorderStyle, "None")
        pop.ShowInTaskbar = False
        pop.StartPosition = FormStartPosition.Manual
        pop.BackColor = bar_bg
        pop.KeyPreview = True
        pm = {"items": [], "hover": -1, "key": "", "closed": 0.0}

        def pop_row(i):
            return Rectangle(MENU_PAD, MENU_PAD + i * MENU_ROW, pop.ClientSize.Width - 2 * MENU_PAD, MENU_ROW - 2)

        def pop_paint(s_, e):
            g = e.Graphics
            g.SmoothingMode = SmoothingMode.AntiAlias
            g.Clear(bar_bg)
            pen = Pen(line)
            g.DrawPath(pen, rounded(Rectangle(0, 0, pop.ClientSize.Width - 1, pop.ClientSize.Height - 1), 10))
            pen.Dispose()
            for i, (k, t, _) in enumerate(pm["items"]):
                r, on = pop_row(i), k == ui["mode"]
                back = accent if on else hover_bg if i == pm["hover"] else bar_bg
                if back != bar_bg:
                    br = SolidBrush(back)
                    g.FillPath(br, rounded(r, 8))
                    br.Dispose()
                col = accent_fg if on else fg
                if on:
                    TextRenderer.DrawText(g, "", f_icon, Point(r.X + 10, r.Y + 11), col, back, TF.NoPadding)
                TextRenderer.DrawText(g, t, f_tab, Point(r.X + 32, r.Y + 8), col, back, TF.NoPadding)

        def pop_at(y):
            i = (y - MENU_PAD) // MENU_ROW
            return i if 0 <= i < len(pm["items"]) and y >= MENU_PAD else -1

        def pop_move(s_, e):
            i = pop_at(e.Y)
            if i != pm["hover"]:
                pm["hover"] = i
                pop.Invalidate()

        def pop_leave(s_, e):
            if pm["hover"] != -1:
                pm["hover"] = -1
                pop.Invalidate()

        def pop_hide(*_):
            if pop.Visible:
                pm["closed"] = time.time()
                pop.Hide()

        def pop_click(s_, e):
            i = pop_at(e.Y)
            if i >= 0:
                h = pm["items"][i][2]
                pop_hide()
                h()

        def pop_key(s_, e):
            if e.KeyCode == Keys.Escape:
                pop_hide()

        pop.Paint += pop_paint
        pop.MouseMove += pop_move
        pop.MouseLeave += pop_leave
        pop.MouseClick += pop_click
        pop.Deactivate += pop_hide
        pop.KeyDown += pop_key

        def open_menu(it):
            # нажатие на ту же кнопку при открытом меню — закрыть (меню уже спряталось по потере фокуса), а не открыть снова
            if pm["key"] == it["key"] and time.time() - pm["closed"] < 0.3:
                return
            pm.update(items=list(it["members"]), hover=-1, key=it["key"])
            r = it["rect"]
            w = max([r.Width] + [TextRenderer.MeasureText(t, f_tab).Width + 32 + 26 + 2 * MENU_PAD for _, t, _ in it["members"]])
            h = len(it["members"]) * MENU_ROW + 2 * MENU_PAD
            pop.Size = Size(w, h)
            pop.Region = Region(rounded(Rectangle(0, 0, w, h), 10))
            pop.Location = bar.PointToScreen(Point(r.X, r.Bottom + 4))
            pop.Show(form)
            pop.Activate()
            pop.Invalidate()

        def rounded(r, rad):
            p, d = GraphicsPath(), rad * 2
            p.AddArc(r.X, r.Y, d, d, 180, 90)
            p.AddArc(r.Right - d, r.Y, d, d, 270, 90)
            p.AddArc(r.Right - d, r.Bottom - d, d, d, 0, 90)
            p.AddArc(r.X, r.Bottom - d, d, d, 90, 90)
            p.CloseFigure()
            return p

        def item_width(g, it):
            if it.get("compact"):       # окно узкое — только иконка
                return 40
            w = 12 + 20 + TextRenderer.MeasureText(g, label(it), f_tab, Size(999, 40), TF.NoPadding).Width + 14
            if it["key"] == "feed" and bar_state["badge"]:
                w += TextRenderer.MeasureText(g, str(bar_state["badge"]), f_badge, Size(99, 20), TF.NoPadding).Width + 16
            return w

        def draw_item(g, it, x):
            w, h, y = item_width(g, it), 34, 10
            r = Rectangle(x, y, w, h)
            it["rect"] = r
            on = it["key"] == ui["mode"] or it.get("active") or in_group(it)
            hov = bar_state["hover"] == it["key"]
            back = accent if on else (hover_bg if hov else bar_bg)
            if on or hov:
                g.FillPath(SolidBrush(back), rounded(r, 9))
            col = accent_fg if on else (fg if it["side"] == "l" else muted)
            TextRenderer.DrawText(g, it["icon"], f_icon, Rectangle(x + 10, y, 20, h), col, back, TF.VerticalCenter | TF.HorizontalCenter)
            if not it.get("compact"):
                TextRenderer.DrawText(g, label(it), f_tab, Point(x + 32, y + 8), col, back, TF.NoPadding)
            if it["key"] == "feed" and bar_state["badge"] and not it.get("compact"):
                t = str(bar_state["badge"])
                tw = TextRenderer.MeasureText(g, t, f_badge, Size(99, 20), TF.NoPadding).Width
                br = Rectangle(r.Right - tw - 22, y + 8, tw + 12, 18)
                g.FillPath(SolidBrush(gold), rounded(br, 9))
                TextRenderer.DrawText(g, t, f_badge, br, accent_fg, gold, TF.VerticalCenter | TF.HorizontalCenter)
            return w

        def on_paint(s, e):
            g = e.Graphics
            g.SmoothingMode = SmoothingMode.AntiAlias
            g.FillRectangle(SolidBrush(line), 0, bar.Height - 1, bar.Width, 1)
            logo = Rectangle(16, 13, 28, 28)
            g.FillPath(SolidBrush(accent), rounded(logo, 8))
            TextRenderer.DrawText(g, "S", f_s, logo, accent_fg, accent, TF.VerticalCenter | TF.HorizontalCenter)
            g.FillEllipse(SolidBrush(gold), 38, 10, 8, 8)
            x = 54
            for part, col in (("Sheben", fg), ("'", gold), ("Store", fg)):
                TextRenderer.DrawText(g, part, f_logo, Point(x, 15), col, bar_bg, TF.NoPadding)
                x += TextRenderer.MeasureText(g, part, f_logo, Size(999, 40), TF.NoPadding).Width
            x += 18
            g.FillRectangle(SolidBrush(line), x, 16, 1, 22)
            x += 14
            for it in items:
                it["compact"] = False
            for group in ([i for i in items if i["side"] == "r"], [i for i in items if i["side"] == "l"]):
                if x + sum(item_width(g, i) + 4 for i in items) + 12 <= bar.Width:
                    break
                for i in group:
                    i["compact"] = True
            for it in items:
                if it["side"] == "l":
                    x += draw_item(g, it, x) + 4
            xr = bar.Width - 12
            for it in reversed(items):
                if it["side"] == "r":
                    xr -= item_width(g, it)
                    draw_item(g, it, xr)
                    xr -= 4

        def hit(x, y):
            return next((it for it in items if it["rect"] is not None and it["rect"].Contains(x, y)), None)

        def on_move(s, e):
            it = hit(e.X, e.Y)
            bar.Cursor = Cursors.Hand if it else Cursors.Default
            k = it["key"] if it else None
            if k != bar_state["hover"]:
                bar_state["hover"] = k
                bar.Invalidate()

        def on_leave(s, e):
            bar_state["hover"] = None
            bar.Invalidate()

        def on_click(s, e):
            it = hit(e.X, e.Y)
            if not it:
                return
            if it.get("members"):
                if in_group(it) or e.X >= it["rect"].Right - 24:     # уже в группе или нажали «▾» — меню
                    open_menu(it)
                else:
                    next(h for k, _, h in it["members"] if k == it["last"])()
                return
            it["handler"]()

        bar.Paint += on_paint
        bar.MouseMove += on_move
        bar.MouseLeave += on_leave
        bar.MouseClick += on_click
        bar.Resize += lambda s, e: bar.Invalidate()

        def paint():
            bar.Invalidate()

        def app_route():
            """Какая страница сейчас реально открыта: ссылки внутри страниц («Открыть ленту» на главной) меняют её сами."""
            from urllib.parse import urlparse
            try:
                return urlparse(str(av.CoreWebView2.Source)).path.strip("/")
            except Exception:
                return None

        KEEP = ("", "learn")     # лента и «Обучение» не перезагружаются при каждом нажатии на вкладку
        pviews = {}

        def page_view(route):
            if route not in pviews:
                v = make_view()
                split.Panel2.Controls.Add(v)

                def ready(s, e, v=v):
                    if not e.IsSuccess:
                        say("Встроенный браузер не запустился:", e.InitializationException)
                        return
                    v.CoreWebView2.NewWindowRequested += a_new_window
                    v.CoreWebView2.WebMessageReceived += a_message
                    v.CoreWebView2.Navigate(FEED + route)
                v.CoreWebView2InitializationCompleted += ready
                v.EnsureCoreWebView2Async(None)
                pviews[route] = v
            return pviews[route]

        def cur_page():
            """Окно страницы приложения, которое сейчас на экране (лента / «Обучение» — свои, остальные — общее)."""
            r = ui.get("want", "") if ui["mode"] != "side" else ""
            return pviews.get(r, av) if r in KEEP else av

        def app_nav(route):
            if route in KEEP:
                page_view(route).BringToFront()
                ui["route"] = route
                return
            av.BringToFront()
            if av.CoreWebView2 is not None and app_route() != route:
                av.CoreWebView2.Navigate(FEED + route)
                ui["route"] = route

        def show(mode, route=""):
            ui["mode"] = mode
            for it_ in items:       # группа запоминает, где вы были: следующее нажатие откроет её
                if any(k == mode for k, _, _ in it_.get("members") or []):
                    it_["last"] = mode
            ui["want"] = "" if mode == "side" else route
            if mode in ("goofish", "watchv") or mode in SITES:
                split.Panel1Collapsed = False
                split.Panel2Collapsed = True
                if mode in SITES:           # Oskelly / Avito / Telegram — обычные вкладки, вы листаете их сами
                    site_view(mode).BringToFront()
                elif mode == "watchv":        # окно автопоиска поверх вашей вкладки Goofish; обе продолжают работать
                    watch_view()
                    watch_status()
                    wpanel.BringToFront()
                else:
                    gv.BringToFront()
            elif mode == "side":
                gv.BringToFront()
                split.Panel1Collapsed = False
                split.Panel2Collapsed = False
                split.SplitterDistance = int(split.Width * 0.58)
                app_nav("")
            else:
                split.Panel2Collapsed = False
                split.Panel1Collapsed = True
                app_nav(route)
            paint()

        def goofish_back():
            if ui["mode"] == "watchv" or ui["mode"] in SITES:
                v = watch["view"] if ui["mode"] == "watchv" else sites.get(ui["mode"])
                if v is not None and v.CoreWebView2 is not None and v.CoreWebView2.CanGoBack:
                    v.CoreWebView2.GoBack()
                return
            if gv.CoreWebView2 is not None and gv.CoreWebView2.CanGoBack:
                gv.CoreWebView2.GoBack()
            if ui["mode"] not in ("goofish", "side"):
                show("goofish")

        def reload_current():
            v = {"goofish": gv, "watchv": watch["view"], **sites}.get(ui["mode"]) or cur_page()
            if v is not None and v.CoreWebView2 is not None:
                v.CoreWebView2.Reload()

        def item(key):
            return next(i for i in items if i["key"] == key)

        # --- уведомления Windows (значок в трее нужен, чтобы их показывать)
        tray = NotifyIcon()
        tray.Icon = form.Icon if (HERE / "app.ico").exists() else SystemIcons.Application
        tray.Text = "Sheben'Store"
        tray.Visible = True
        note = {"url": None}

        def notify(title, text, url=None):
            note["url"] = url
            tray.ShowBalloonTip(6000, title[:60], text[:250] or " ", ToolTipIcon.Info)

        def on_balloon(s, e):
            if note["url"] and gv.CoreWebView2 is not None:
                gv.CoreWebView2.Navigate(note["url"])
                if ui["mode"] != "side":
                    show("goofish")
            else:
                show("feed", "")
            if form.WindowState == FormWindowState.Minimized:
                form.WindowState = FormWindowState.Maximized
            form.Activate()
        tray.BalloonTipClicked += on_balloon
        tray.DoubleClick += lambda s, e: form.Activate()

        # находки копятся и приходят одной сводкой: раз в NOTE_EVERY с, а при автопоиске — в конце круга
        NOTE_EVERY = 180
        pend = {"items": [], "last": time.time(), "flush_at": 0.0}

        def show_alerts(new):
            # проверка, утренняя сводка и «поиски из каналов обновлены» — сразу
            now_ = [x for x in new if x.get("kind") in ("test", "summary", "info")]
            for x in now_:
                notify({"test": "Проверка уведомлений", "info": "Sheben'Store"}.get(x["kind"], "Сводка Sheben'Store"),
                       x.get("text", ""))
            if now_:
                threading.Thread(target=tg_alerts, args=(now_,), daemon=True).start()
            finds = [x for x in new if x.get("kind") not in ("test", "summary", "info")]
            pend["items"] += finds
            if finds and watch["on"]:   # кто первый написал продавцу, тот и купил: не ждём конца круга, ~20 с на соседние находки
                pend["flush_at"] = min(pend["flush_at"] or float("inf"), time.time() + 20)

        def flush_alerts():
            new, since = pend["items"], pend["last"]
            pend.update(items=[], last=time.time(), flush_at=0.0)
            if not new:
                return
            fmt = lambda v: f"{v:,.0f}".replace(",", " ")
            prof = lambda x: x["profit_rub"] if isinstance(x.get("profit_rub"), (int, float)) else -1e9
            # заказы клиентов, потом снижения и перевыставления, потом новые
            new.sort(key=lambda x: (x.get("kind") != "order", x.get("kind") == "new", -prof(x)))
            threading.Thread(target=tg_alerts, args=(new,), daemon=True).start()
            if len(new) == 1:
                x, p = new[0], new[0].get("profit_rub")
                head = {"drop": "Цена снижена: ", "relist": "Перевыставлено дешевле: ",
                        "order": f"Заказ {x.get('text', '')}: "}.get(x.get("kind"), "Выгодная вещь: ")
                pr = f" · прибыль {'+' if p > 0 else ''}{fmt(p)} ₽" if isinstance(p, (int, float)) else ""
                old = f" (было ¥{fmt(x['old_price'])})" if x.get("old_price") else ""
                price = x.get("price_txt") or f"¥{fmt(x['price_cny'])}{old}"
                notify(head + x["title"], f"{price}{pr}" + (f" · выложено {x['age']} назад" if x.get("age") else "")
                       + (f" · «{x['q']}»" if x.get("q") else ""), x.get("url"))
                return
            mark = {"drop": "↓ ", "relist": "↻ "}
            head = "Автопоиск" if watch["on"] else f"За {round((time.time() - since) / 60) or 1} мин"
            n_ord = sum(x.get("kind") == "order" for x in new)
            notify(f"{head}: находок {len(new)}" + (f", под заказ {n_ord}" if n_ord else ""),
                   "; ".join((f"заказ {x.get('text', '')}: " if x.get("kind") == "order" else mark.get(x.get("kind"), ""))
                             + f"{x['title']} " + (x.get("price_txt") or f"¥{fmt(x['price_cny'])}") for x in new[:4]))

        def note_tick(s, e):
            now = time.time()
            if watch["on"]:
                if pend["flush_at"] and now >= pend["flush_at"]:
                    flush_alerts()
            elif now - pend["last"] >= NOTE_EVERY:
                flush_alerts()

        def search_url(q):
            return GOOFISH + "search?q=" + quote(q)

        def entry_url(x):
            """Строка «Моих поисков»: поиск или профиль продавца («Мои продавцы», числовой id). Mercari — сразу
            «сначала новые» и только в продаже (на Goofish сортировку адресом не задать)."""
            if x.get("site") == "mercari":
                return (MERCARI + "user/profile/" + x["seller"] if x.get("seller") else
                        MERCARI + "search?keyword=" + quote(x["q"]) + "&status=on_sale&sort=created_time&order=desc"
                        + (f"&price_min={int(x['min'])}" if x.get("min") else ""))
            return GOOFISH + "personal?userId=" + x["seller"] if x.get("seller") else search_url(x["q"])

        # --- «След. поиск»: сохранённые поиски по очереди во вкладке Goofish (по вашему клику)
        srch = {"i": -1}

        def next_search():
            lst = server_info("searches", 2).get("items") or []
            if not lst:
                notify("Мои поиски пусты", "Добавьте запросы на «Главной» — раздел «Мои поиски».")
                show("home", "home")
                return
            srch["i"] = (srch["i"] + 1) % len(lst)
            item("next")["text"] = f"Поиск {srch['i'] + 1}/{len(lst)}"
            x = lst[srch["i"]]
            if x.get("site") == "mercari":      # поиск Mercari — во вкладке Mercari
                v = site_view("mercari")
                if v.CoreWebView2 is not None:
                    v.CoreWebView2.Navigate(entry_url(x))
                else:
                    site_goto["mercari"] = entry_url(x)
                show("mercari")
            else:
                if gv.CoreWebView2 is not None:
                    gv.CoreWebView2.Navigate(entry_url(x))
                if ui["mode"] != "side":
                    show("goofish")
            paint()

        # --- «Следить»: автопоиск в отдельной вкладке «Автопоиск» (ваша вкладка Goofish не трогается).
        # Каждый поиск: открыть выдачу и пролистать до глубины с «Главной» (в «综合» на 2–3-й странице новых больше,
        # чем на 1-й). «新发布 → 最新» скриптом не выбираем: на первой же попытке Goofish ответил проверкой.
        # Без маскировки и прокси; при проверке/входе — сразу стоп, показываем вкладку «Автопоиск» и зовём вас.
        WATCH_GAP = config.cfg("watch_gap")         # секунд между поисками
        PAGE_GAP = config.cfg("page_gap")           # секунд до следующей страницы того же поиска
        MERCARI_GAP = config.cfg("mercari_gap")     # после шага Mercari: другой сайт, Goofish между своими запросами всё равно ждёт
        JITTER = config.cfg("jitter")               # случайная прибавка к каждой паузе: запросы не идут через ровные промежутки
        blocks = gp_throttle.BlockLog(HERE / "out" / "watch_blocks.json")      # ограничения сайта за сутки -> нарастающая пауза
        JUNK_EVERY = 3          # поиски «мало толку» и с пометкой «rare» — только в каждом 3-м круге
        SLOW_EVERY = 4          # поиски, давно не приносившие сильных вещей (решает сервер), — в каждом 4-м
        NIGHT_SLOW = config.cfg("night_slow")       # в тихие часы по Пекину (новых почти нет) паузы во столько раз длиннее
        NEXT_PAGE = r"""(() => {
          const s = document.querySelector('[class*="search-page-tiny-page"]');
          if (!s) return "нет переключателя страниц";
          const m = s.textContent.match(/(\d+)\s*\/\s*(\d+)/);
          if (m && +m[1] >= +m[2]) return "последняя страница";
          const b = s.nextElementSibling;
          if (!b) return "нет кнопки следующей страницы";
          b.click();
          return "ok";
        })()"""
        CHECK_HINT = ("Если на странице ниже есть проверка (ползунок, картинка) — пройдите её сами. Если нет — подождите "
                      "и нажмите «Обновить». Автопоиск продолжит сам, когда пройдёт назначенная пауза и поиск снова откроется.")
        watch = {"on": False, "since": 0.0, "queue": [], "next_at": 0.0, "cycle_at": 0.0, "view": None, "js": None, "cycle": 0,
                 "quiet": list(range(1, 8)), "cur": None, "done": 0, "total": 0, "log": [], "stopped": "", "hint": "",
                 "resume": False}

        def china_quiet() -> bool:
            return (time.gmtime().tm_hour + 8) % 24 in watch["quiet"]

        def step_gap(cur, nxt) -> int:
            """Пауза после шага cur (q, стр., глубина, адрес, площадка) перед nxt. Goofish -> Goofish 14 с (страница 10 с);
            рядом с Mercari короче: между двумя запросами к Goofish всё равно проходит не меньше 10 + 9 с."""
            if nxt and nxt[1] > 1:
                return PAGE_GAP
            if cur[4] == "mercari":
                return MERCARI_GAP
            return PAGE_GAP if nxt and nxt[4] == "mercari" else WATCH_GAP

        def queue_eta() -> list:
            """Через сколько секунд после ближайшего шага (next_at) откроется каждый поиск очереди: [q, площадка, с]."""
            out, t, qu = [], 0, watch["queue"]
            slow = NIGHT_SLOW if china_quiet() else 1
            for i, x in enumerate(qu):
                if x[1] == 1:
                    out.append([x[0], x[4], t])
                t += step_gap(x, qu[i + 1] if i + 1 < len(qu) else None) * slow
            return out

        # вкладка «Автопоиск»: окно автопоиска и над ним строка «что делает сейчас / что нашёл»
        wpanel, wstrip, w_state, w_hint, w_last = Panel(), Panel(), Label(), Label(), Label()
        wpanel.Dock = DockStyle.Fill
        wpanel.BackColor = bar_bg
        wstrip.Dock = DockStyle.Top
        wstrip.BackColor = bar_bg
        wstrip.Padding = Padding(16, 6, 16, 4)
        # Dock=Top: добавленный позже встаёт выше — снизу вверх «последние», подсказка, состояние
        for lb, font, col in ((w_last, Font("Segoe UI", 9), muted), (w_hint, Font("Segoe UI", 9), gold),
                              (w_state, Font("Segoe UI Semibold", 10), fg)):
            lb.Dock = DockStyle.Top
            lb.Height = 20
            lb.AutoEllipsis = True
            lb.Font = font
            lb.ForeColor = col
            wstrip.Controls.Add(lb)
        wpanel.Controls.Add(wstrip)
        split.Panel1.Controls.Add(wpanel)
        wpanel.SendToBack()             # под вашей вкладкой Goofish, пока не открыта

        def watch_view():
            if watch["view"] is None:
                v = make_view()
                wpanel.Controls.Add(v)
                v.BringToFront()        # занимает всё под строкой состояния
                v.CoreWebView2InitializationCompleted += w_ready
                v.EnsureCoreWebView2Async(None)
                watch["view"] = v
            return watch["view"]

        def watch_status():
            """Строка над вкладкой «Автопоиск». Зовётся раз в секунду, пока следит, и при каждом событии."""
            now = time.time()
            if watch["on"]:
                parts, cur = [f"Следит · круг {watch['cycle']}"], watch["cur"]
                if cur:
                    parts.append(f"поиск {watch['done']} из {watch['total']}: " + ("Mercari " if cur[3] == "mercari" else "")
                                 + f"«{cur[0]}»" + (f", стр. {cur[1]} из {cur[2]}" if cur[2] > 1 else ""))
                jp = next((x for x in queue_eta() if x[1] == "mercari"), None)
                if jp and not (cur and cur[3] == "mercari"):
                    parts.append(f"Mercari через ~{max(1, round((watch['next_at'] - now + jp[2]) / 60))} мин")
                if not watch["queue"] and now < watch["cycle_at"]:
                    parts.append("круг пройден, следующий в " + time.strftime("%H:%M", time.localtime(watch["cycle_at"])))
                elif watch["next_at"] > now:
                    parts.append(f"следующий шаг через {int(watch['next_at'] - now + 0.99)} с")
                if china_quiet():
                    parts.append(f"ночь в Китае, паузы ×{NIGHT_SLOW}")
                state, hint = " · ".join(parts), ""
            else:
                state = watch["stopped"] or "Выключен"
                hint = watch["hint"] or ("«Следить» справа вверху включает автопоиск. Здесь видно, что он открывает; "
                                         "ваша вкладка Goofish при этом не трогается.")
            last = ("Последние: " + " · ".join(reversed(watch["log"]))) if watch["log"] else \
                "Здесь появятся результаты: сколько объявлений на странице и сколько из них новых."
            for lb, text in ((w_state, state), (w_hint, hint), (w_last, last)):
                if lb.Text != text:
                    lb.Text = text
            if w_hint.Visible != bool(hint):
                w_hint.Visible = bool(hint)
            h = 10 + 20 * (3 if hint else 2)
            if wstrip.Height != h:
                wstrip.Height = h
            push_watch()

        def push_watch():
            """Состояние автопоиска -> сервер, для живой строки на «Главной». Только при изменении (не раз в секунду)."""
            cur = watch["cur"]
            st = {"on": watch["on"], "cycle": watch["cycle"], "cur": list(cur) if cur else None, "done": watch["done"],
                  "total": watch["total"], "next_at": round(watch["next_at"], 1), "gap": watch.get("gap", 0),
                  "cycle_at": round(watch["cycle_at"]) if not watch["queue"] else 0, "quiet": china_quiet(),
                  "stopped": watch["stopped"], "log": list(reversed(watch["log"])), "queue": queue_eta(),
                  "plan": watch.get("plan", [])}
            sig = json.dumps(st, ensure_ascii=False)
            if sig != watch.get("pushed"):
                watch["pushed"] = sig
                threading.Thread(target=post_json, args=("watch/state", st), daemon=True).start()

        def w_ready(sender, e):
            if not e.IsSuccess:
                stop_watch("Встроенный браузер автопоиска не запустился.")
                return
            core = watch["view"].CoreWebView2
            core.AddScriptToExecuteOnDocumentCreatedAsync(hook)
            core.WebMessageReceived += w_message

            def no_popup(s, ev):
                ev.Handled = True
            core.NewWindowRequested += no_popup

            def w_done(s, ev):
                src = str(core.Source)
                if watch["on"] and any(h in src.lower() for h in ("login", "punish", "captcha")):
                    stop_watch("Goofish просит проверку", check=True)
            core.NavigationCompleted += w_done

        def w_message(sender, e):
            raw = e.TryGetWebMessageAsString()
            if not raw.startswith('{"market"'):
                threading.Thread(target=w_send, args=(raw,), daemon=True).start()

        def w_send(raw):
            res = send_items(raw)
            cur = watch["cur"]
            if res.get("queued"):       # сервер занят: ответ не потерян, уйдёт повтором
                note = time.strftime("%H:%M") + " сервер занят — ответ в очереди повтора"
                watch["log"] = (watch["log"] + [note])[-6:]
                return
            if res.get("blocked"):
                note = time.strftime("%H:%M") + " проверка Goofish"
            elif res.get("search") or res.get("profile"):
                q = res.get("q", "")
                if res.get("profile") and cur:      # «профиль продавца» -> «продавец Ник» из «Моих поисков»
                    q = cur[0]
                page = f" стр.{cur[1]}" if cur and cur[0] == q and cur[2] > 1 else ""
                note = f"«{q}»{page}: " + (f"{res['n']}, новых {res.get('added', 0)}" if res.get("n") else "пусто")
            else:
                return      # рекомендации внизу выдачи, подсказки и прочие запросы страницы
            watch["log"] = (watch["log"] + [note])[-6:]
            search_ok(res)
            try:
                form.BeginInvoke(Action(watch_status))
            except Exception:
                pass

        def run_summary_note():
            try:
                with urllib.request.urlopen(FEED + "run/summary?reset=1", timeout=20) as r:
                    text = json.loads(r.read()).get("text", "")
                if text:
                    watch["log"] = (watch["log"] + [time.strftime("%H:%M ") + text.replace("Сводка прогона", "круг")])[-6:]
            except Exception as e:
                say("сводка прогона не получена:", repr(e)[:120])

        def search_ok(res):
            """Поиск снова отдаёт объявления (в любой из вкладок) — проверка пройдена, автопоиск можно продолжать."""
            # после ограничения — не раньше назначенной паузы (она растёт с каждым ограничением за сутки)
            if res and res.get("search") and res.get("n") and watch["resume"] and not watch["on"]                     and time.time() >= watch.get("resume_at", 0):
                watch["resume"] = False
                try:
                    form.BeginInvoke(Action(resume_watch))
                except Exception:
                    pass

        def w_retry_done(res):      # ответ, не принятый сразу, дошёл повтором — строка в журнал автопоиска
            if res.get("search") or res.get("profile"):
                watch["log"] = (watch["log"] + [f"«{res.get('q', '')}»: {res.get('n', 0)}, новых {res.get('added', 0)} (повтор)"])[-6:]
                search_ok(res)
        RETRY_DONE.append(w_retry_done)

        def watch_tick(s, e):
            now = time.time()
            watch_status()
            if not watch["on"] or now < watch["next_at"]:
                return
            v = watch_view()
            if v.CoreWebView2 is None:      # ещё запускается
                return
            js = watch["js"]
            if js is not None and js[2].IsCompleted:
                watch["js"] = None
                res = str(js[2].Result if not js[2].IsFaulted else "ошибка скрипта").strip('"')
                if res != "ok":
                    say(f"Автопоиск «{js[0]}», стр. {js[1]}: {res}")
            slow = NIGHT_SLOW if china_quiet() else 1
            if not watch["queue"]:
                if now < watch["cycle_at"]:     # круг прошёл быстрее заданного интервала — ждём
                    watch["next_at"] = watch["cycle_at"]
                    return
                cfg = server_info("searches", 2)
                if not cfg:     # сервер занят и не ответил за 2 с — это не «поиски пусты»: попробуем через 5 с
                    watch["next_at"] = now + 5
                    return
                items = cfg.get("items") or []
                watch["quiet"] = cfg.get("quiet") or watch["quiet"]
                # «раз в 3 круга» и «мало толку»: каждый круг берём свою треть (по запросу), а не все разом
                # в каждом третьем — иначе этот круг вдвое длиннее (ночью по Китаю до ~45 мин)
                # «slow» (сервер: 3 дня без вещей с оценкой 70+) — так же, но раз в SLOW_EVERY круга
                def due(x):
                    h = sum(map(ord, x["q"]))
                    if x.get("junk"):
                        return h % JUNK_EVERY == watch["cycle"] % JUNK_EVERY
                    return not x.get("slow") or h % SLOW_EVERY == watch["cycle"] % SLOW_EVERY
                if any(not x.get("junk") and not x.get("slow") for x in items):
                    items = [x for x in items if due(x)]
                # порядок площадок — «в круге» на «Главной»; по умолчанию вперемешку: Mercari равномерно между Goofish
                gf = [x for x in items if x.get("site") != "mercari"]
                mc = [x for x in items if x.get("site") == "mercari"]
                mix, j = [], 0
                for i, x in enumerate(gf):
                    while j < len(mc) and j * len(gf) <= i * len(mc):
                        mix.append(mc[j])
                        j += 1
                    mix.append(x)
                mix += mc[j:]
                items = {"goofish_first": gf + mc, "mercari_first": mc + gf, "goofish_only": gf,
                         "mercari_only": mc}.get(cfg.get("order"), mix)
                watch["cycle"] += 1
                if watch["cycle"] > 1:      # прошлый круг закончен — сводка в журнал и в ленту событий автопоиска
                    threading.Thread(target=run_summary_note, daemon=True).start()
                # у поиска два варианта (иероглифы / латиница), лучший ещё не выбран — чередуем по кругам
                items = [dict(x, q=x["alt"]) if x.get("alt") and not x.get("picked") and watch["cycle"] % 2 == 0 else x
                         for x in items]
                watch["queue"] = [(x["q"], p, x.get("depth") or 1, entry_url(x), x.get("site", ""))
                                  for x in items for p in range(1, (x.get("depth") or 1) + 1)]
                watch["plan"] = [[x["q"], x.get("site", "")] for x in items]     # весь круг — для кольца на «Главной»
                if not watch["queue"]:
                    stop_watch("Мои поиски пусты — добавьте запросы на «Главной».")
                    return
                watch.update(cycle_at=now + 60 * cfg.get("interval_min", 15) * slow, done=0, total=len(items))
            step = watch["queue"].pop(0)
            q, page, depth, url, site = step
            watch["cur"] = (q, page, depth, site)
            if page == 1:
                watch["done"] += 1
                v.CoreWebView2.Navigate(url)
            else:
                watch["js"] = (q, page, v.CoreWebView2.ExecuteScriptAsync(NEXT_PAGE))
            gap = step_gap(step, watch["queue"][0] if watch["queue"] else None)
            pause = gp_throttle.jittered(gap * slow, JITTER)
            watch["next_at"] = now + pause
            watch["gap"] = pause
            item("watch")["text"] = "Следит" + (" · ночь в Китае" if slow > 1 else "") + " · " + time.strftime("%H:%M")
            watch_status()
            paint()

        wtimer = Timer()
        wtimer.Interval = 1000      # шаг 1 с: при 5 с паузы 14/10 округлялись бы до 15/10
        wtimer.Tick += watch_tick

        def start_watch(delay=0.0):
            watch.update(on=True, since=time.time(), queue=[], next_at=time.time() + delay, cycle_at=0.0, cycle=0,
                         cur=None, done=0, total=0, stopped="", hint="", resume=False)
            it = item("watch")
            it["active"], it["text"] = True, "Следит"
            wtimer.Start()
            watch_status()
            paint()

        def kick_watch():
            """«Обновить» у «Моих поисков»: круг пройден и ждёт интервала — начать следующий сразу (со свежим списком)."""
            if watch["on"] and not watch["queue"]:
                watch.update(cycle_at=0.0, next_at=time.time())
                watch_status()

        def toggle_watch():
            if watch["on"]:
                stop_watch()
                return
            start_watch()
            notify("Автопоиск включён", f"«Мои поиски» проверяются по кругу: ~{WATCH_GAP} с на поиск и {PAGE_GAP} с "
                   f"на каждую следующую страницу, ночью по Китаю в {NIGHT_SLOW} раза реже. Что он делает — "
                   "во вкладке «Автопоиск». Если Goofish попросит проверку, автопоиск сам выключится.")

        def resume_watch():
            if watch["on"]:
                return
            start_watch(60)
            notify("Автопоиск снова включён", "Поиск Goofish снова открывается — продолжу «Мои поиски» через минуту.")

        def stop_watch(reason=None, check=False):
            """check: Goofish просит проверку — страницу не закрываем и показываем её во вкладке «Автопоиск»."""
            plan = None
            if check:       # ограничение сайта: пауза растёт с каждым случаем за сутки, после N случаев — стоп до вашей команды
                plan = blocks.plan(time.time(), config.cfg("backoff_base_min"), config.cfg("backoff_factor"),
                                   config.cfg("backoff_max_min"), config.cfg("block_max_per_day"))
                if plan["halt"]:
                    reason = (f"{reason}. Это {plan['n']}-е ограничение за сутки — автопоиск остановлен до вашей команды "
                              "(кнопка «Следить»). Сегодня Goofish лучше не трогать")
                else:
                    reason = (f"{reason}. Ограничение №{plan['n']} за сутки: продолжу не раньше "
                              f"{time.strftime('%H:%M', time.localtime(plan['resume_at']))} (пауза {plan['wait_min']:.0f} мин)")
                say(f"Автопоиск: ограничение Goofish №{plan['n']} за сутки, пауза {plan['wait_min']:.0f} мин"
                      + (", дальше только по вашей команде" if plan["halt"] else ""))
            watch.update(on=False, queue=[], cur=None, resume=bool(check and not plan["halt"]) if plan else False,
                         resume_at=plan["resume_at"] if plan else 0.0, hint=CHECK_HINT if check else "",
                         stopped=f"Остановлен в {time.strftime('%H:%M')}: {reason}" if reason
                         else f"Выключен вами в {time.strftime('%H:%M')}")
            wtimer.Stop()
            it = item("watch")
            it["active"], it["text"] = False, "Следить"
            v = watch["view"]
            if not check and v is not None and v.CoreWebView2 is not None:
                v.CoreWebView2.Navigate("about:blank")
            watch_status()
            paint()
            if reason:
                text = reason + (". Откройте вкладку «Автопоиск». " + CHECK_HINT if check else "")
                notify("Автопоиск выключен", text)
                threading.Thread(target=tg_alerts, args=([{"kind": "stop", "text": "Автопоиск выключен. " + text}],), daemon=True).start()
                call_user()
                if check:
                    show("watchv")

        def call_user():
            """Капча/ограничение: звук и окно поверх остальных, чтобы вы сразу увидели."""
            try:
                from System.Media import SystemSounds
                SystemSounds.Exclamation.Play()
                if form.WindowState == FormWindowState.Minimized:
                    form.WindowState = FormWindowState.Normal
                form.TopMost = True
                form.TopMost = False
                form.Activate()
            except Exception as ex:
                say("Не удалось показать окно:", repr(ex)[:120])

        def hidden_view(ready):
            """Ещё одна невидимая вкладка (под вкладкой Goofish), с той же сетью и папкой данных."""
            v = make_view()
            split.Panel1.Controls.Add(v)
            v.SendToBack()
            v.CoreWebView2InitializationCompleted += ready
            v.EnsureCoreWebView2Async(None)
            return v

        # --- вкладки площадок рядом с Goofish: открываются при первом нажатии, вы листаете их сами.
        # Oskelly — Щебень читает карточки со страниц, которые вы открыли (как раньше во вкладке Goofish); Avito и Telegram
        # ничего не читает и ничего на них не делает. Вход в аккаунты — только ваш.
        SITES = {"mercari": ("Mercari", "", MERCARI),
                 "osk": ("Oskelly", "", "https://oskelly.ru/"), "avito": ("Avito", "", "https://www.avito.ru/"),
                 "tg": ("Telegram", "", "https://web.telegram.org/a/")}
        sites, site_goto = {}, {}

        def site_view(key):
            if key not in sites:
                v = make_view()
                split.Panel1.Controls.Add(v)

                def ready(s, e, v=v, url=SITES[key][2]):
                    if not e.IsSuccess:
                        say(f"Вкладка {SITES[key][0]} не запустилась:", e.InitializationException)
                        return
                    core = v.CoreWebView2

                    def new_window(s2, ev):     # ссылки «в новом окне» — здесь же («Назад» вернёт)
                        ev.Handled = True
                        core.Navigate(ev.Uri)
                    core.NewWindowRequested += new_window
                    if key == "mercari":        # как вкладка Goofish: ответы страницы -> лента, оценка -> панель
                        core.AddScriptToExecuteOnDocumentCreatedAsync(hook)
                        core.AddScriptToExecuteOnDocumentCreatedAsync(card_panel)
                        core.WebMessageReceived += page_message(lambda v=v: v)
                    if key in ("osk", "avito"):     # карточки Oskelly / Avito -> «Цены» (SHOP_EXTRACT / AVITO_EXTRACT)
                        core.WebMessageReceived += lambda s3, ev3: threading.Thread(
                            target=send_from_goofish_tab, args=(ev3.TryGetWebMessageAsString(),), daemon=True).start()
                    core.Navigate(site_goto.pop(key, url))     # «След. поиск» мог попросить страницу до запуска вкладки
                v.CoreWebView2InitializationCompleted += ready
                v.EnsureCoreWebView2Async(None)
                sites[key] = v
            return sites[key]

        # --- «Рынок»: публичные веб-версии TG-каналов (t.me/s/…), по странице раз в 10 с, раз в N дней
        MKT_GAP = 10
        mkt = {"view": None, "on": False, "queue": [], "ch": None, "page": 0, "pages": 5, "before": None,
               "next_at": 0.0, "extract_at": 0.0, "wait": False}

        def m_ready(sender, e):
            if not e.IsSuccess:
                market_finish("встроенный браузер не запустился")
                return
            core = mkt["view"].CoreWebView2

            def no_popup(s, ev):
                ev.Handled = True
            core.NewWindowRequested += no_popup

            def m_done(s, ev):
                if not mkt["wait"]:
                    return
                if ev.IsSuccess:
                    mkt["extract_at"] = time.time() + 2
                else:
                    post_json("market/items", {"market": "tg:" + str(mkt["ch"]), "items": [],
                                               "error": f"страница не открылась ({ev.WebErrorStatus})"})
                    channel_done()
            core.NavigationCompleted += m_done

            def m_msg(s, ev):
                try:
                    data = json.loads(ev.TryGetWebMessageAsString())
                except Exception:
                    return
                threading.Thread(target=post_json, args=("market/items", data), daemon=True).start()
                items = data.get("items") or []
                mkt["page"] += 1
                if not items or not data.get("oldest") or data.get("oldest") == mkt["before"] or mkt["page"] >= mkt["pages"]:
                    channel_done()
                else:
                    mkt.update(before=data["oldest"], wait=False, next_at=time.time() + MKT_GAP)
            core.WebMessageReceived += m_msg

        def channel_done():
            mkt.update(ch=None, wait=False, extract_at=0.0, next_at=time.time() + MKT_GAP)

        def start_market():
            if mkt["on"]:
                return
            cfg = server_info("market/config", 5)
            if not cfg.get("channels"):
                return
            post_json("market/started", {})
            mkt.update(on=True, queue=list(cfg["channels"]), pages=int(cfg.get("pages", 5)), ch=None, next_at=0.0)
            if mkt["view"] is None:
                mkt["view"] = hidden_view(m_ready)
            mtimer.Start()

        def market_finish(err=""):
            mkt.update(on=False, ch=None, wait=False)
            mtimer.Stop()
            v = mkt["view"]
            if v is not None and v.CoreWebView2 is not None:
                v.CoreWebView2.Navigate("about:blank")
            if err:
                say("Рынок:", err)
            threading.Thread(target=post_json, args=("market/done", {}), daemon=True).start()

        def market_tick(s, e):
            now = time.time()
            v = mkt["view"]
            if not mkt["on"] or v is None or v.CoreWebView2 is None:
                return
            if mkt["wait"]:
                if mkt["extract_at"] and now >= mkt["extract_at"]:
                    mkt["extract_at"] = 0.0
                    v.CoreWebView2.ExecuteScriptAsync(TG_EXTRACT.replace("__SRC__", "tg:" + mkt["ch"]))
                elif now > mkt["next_at"] + 60:      # страница не ответила — следующий канал
                    channel_done()
                return
            if now < mkt["next_at"]:
                return
            if mkt["ch"] is None:
                if not mkt["queue"]:
                    market_finish()
                    return
                mkt.update(ch=mkt["queue"].pop(0), page=0, before=None)
            url = f"https://t.me/s/{mkt['ch']}" + (f"?before={mkt['before']}" if mkt["before"] else "")
            mkt.update(wait=True, next_at=now)
            v.CoreWebView2.Navigate(url)

        mtimer = Timer()
        mtimer.Interval = 1000
        mtimer.Tick += market_tick

        # --- Oskelly / Second Friend: читаем карточки со страницы, которую вы сами открыли во вкладке Goofish
        def shop_tick(s, e):
            core = gv.CoreWebView2
            if core is None:
                return
            src = str(core.Source)
            if any(h in src for h in SHOP_HOSTS):
                core.ExecuteScriptAsync(SHOP_EXTRACT)
            ov = sites.get("osk")       # и вкладка Oskelly, если открыта и на ней страница с карточками
            if ov is not None and ov.CoreWebView2 is not None and ui["mode"] == "osk"                     and any(h in str(ov.CoreWebView2.Source) for h in SHOP_HOSTS):
                ov.CoreWebView2.ExecuteScriptAsync(SHOP_EXTRACT)
            av_ = sites.get("avito")    # вкладка Avito: читаем только открытую вами страницу, пока вкладка на экране
            if av_ is not None and av_.CoreWebView2 is not None and ui["mode"] == "avito"                     and "avito.ru" in str(av_.CoreWebView2.Source):
                av_.CoreWebView2.ExecuteScriptAsync(AVITO_EXTRACT)
        stimer = Timer()
        stimer.Interval = 5000
        stimer.Tick += shop_tick

        # --- Telegram: отправка через браузер приложения (Python с этого ПК до api.telegram.org не достаёт)
        tg = {"view": None, "ready": False, "pending": []}

        def t_ready(sender, e):
            if not e.IsSuccess:
                post_json("tg/status", {"ok": False, "error": "встроенный браузер не запустился"})
                return
            core = tg["view"].CoreWebView2

            def t_done(s, ev):
                tg["ready"] = bool(ev.IsSuccess)
                if not ev.IsSuccess:
                    post_json("tg/status", {"ok": False, "error": f"api.telegram.org не открывается ({ev.WebErrorStatus})"})
                    tg["pending"].clear()
                    return
                while tg["pending"]:
                    core.ExecuteScriptAsync(tg["pending"].pop(0))
            core.NavigationCompleted += t_done

            def t_msg(s, ev):
                try:
                    data = json.loads(ev.TryGetWebMessageAsString())
                except Exception:
                    return
                if data.get("chat_id"):
                    threading.Thread(target=post_json, args=("tg/chat", {"chat_id": data["chat_id"]}), daemon=True).start()
                threading.Thread(target=post_json, args=("tg/status", data), daemon=True).start()
            core.WebMessageReceived += t_msg
            # страница самого api.telegram.org: запросы к API идут с того же адреса, без ограничений CORS
            core.Navigate("https://api.telegram.org/bot0/getMe")

        def tg_send(msgs, cfg):
            if not msgs:
                return
            js = (TG_SEND.replace("__TOKEN__", json.dumps(cfg["token"])).replace("__CHAT__", json.dumps(cfg.get("chat_id") or ""))
                  .replace("__PAYLOAD__", json.dumps(msgs, ensure_ascii=False)))
            if tg["view"] is None:
                tg["pending"].append(js)
                tg["view"] = hidden_view(t_ready)
            elif tg["ready"]:
                tg["view"].CoreWebView2.ExecuteScriptAsync(js)
            else:
                tg["pending"].append(js)

        def tg_alerts(new):
            cfg = server_info("tg/config", 2)
            if not cfg.get("token"):
                return
            mp = cfg.get("min_profit") or 0
            msgs = [tg_message(x) for x in new
                    if x.get("kind") != "new" or not mp or (isinstance(x.get("profit_rub"), (int, float)) and x["profit_rub"] >= mp)]
            form.BeginInvoke(Action(lambda: tg_send(msgs[:8], cfg)))

        button("home", "Главная", "\ue80f", lambda: show("home", "home"))
        group("g_sites", "Площадки", "\ue774", [("goofish", "Goofish", lambda: show("goofish")),
                                                 ("mercari", "Mercari", lambda: show("mercari")),
                                                 ("watchv", "Автопоиск", lambda: show("watchv"))])
        button("feed", "Лента", "\ue8fd", lambda: show("feed", ""))
        group("g_ru", "Рынок России", "\ue8ec", [("prices", "Цены", lambda: show("prices", "prices")),
                                                  ("osk", "Oskelly", lambda: show("osk")),
                                                  ("avito", "Avito", lambda: show("avito")),
                                                  ("tg", "Telegram", lambda: show("tg"))])
        group("g_more", "Ещё", "\ue8f1", [("catalog", "Каталог", lambda: show("catalog", "catalog")),
                                           ("deals", "Сделки", lambda: show("deals", "deals")),
                                           ("learn", "Обучение", lambda: show("learn", "learn")),
                                           ("status", "Статус", lambda: show("status", "status"))])
        button("next", "След. поиск", "", next_search, "r")
        button("watch", "Следить", "", toggle_watch, "r")
        button("side", "Рядом","", lambda: show("side"), "r")
        button("back", "Назад", "", goofish_back, "r")
        button("reload", "Обновить", "", reload_current, "r")

        # --- Goofish и Mercari: перехват ответов поиска до загрузки страницы; оценку — обратно на панель этой вкладки
        def page_message(get_view):
            return lambda sender, e: on_message(sender, e, get_view)

        def on_message(sender, e, get_view=lambda: gv):
            raw = e.TryGetWebMessageAsString()

            def work():
                res = send_from_goofish_tab(raw)
                # проверку можно пройти и в своей вкладке: поиск там снова работает — автопоиск продолжит
                search_ok(res)
                # карточка товара: оценку — на панель Щебня поверх страницы (ui/card_panel.js)
                if res.get("follow_import"):
                    n, t = res["follow_import"], res.get("follow_total", 0)
                    try:
                        form.BeginInvoke(Action(lambda: notify("Подписки добавлены", f"В «Мои продавцы» добавлено: {n} "
                                                               f"(всего продавцов {t}). Автопоиск смотрит их профили раз в 3 круга.")))
                    except Exception:
                        pass
                back = {"sheben_card": res["card"]} if res.get("card") else \
                    {"sheben_follow": res["follow"]} if res.get("follow") else None
                if back:
                    msg = json.dumps(back, ensure_ascii=False)

                    def post():
                        v = get_view()
                        if v is not None and v.CoreWebView2 is not None:
                            v.CoreWebView2.PostWebMessageAsJson(msg)
                    try:
                        form.BeginInvoke(Action(post))
                    except Exception:
                        pass
            threading.Thread(target=work, daemon=True).start()

        def g_ready(sender, e):
            if not e.IsSuccess:
                say("Встроенный браузер не запустился:", e.InitializationException)
                return
            core = gv.CoreWebView2
            core.AddScriptToExecuteOnDocumentCreatedAsync(hook)
            core.AddScriptToExecuteOnDocumentCreatedAsync(card_panel)    # только здесь: в «Автопоиске» панель не нужна
            core.WebMessageReceived += on_message

            # Goofish открывает профиль продавца и карточки в новой вкладке: без этого они уходили
            # в отдельное окно, где ответы сайта не перехватываются. Открываем здесь же («Назад» вернёт).
            def g_new_window(s, ev):
                ev.Handled = True
                core.Navigate(ev.Uri)
            core.NewWindowRequested += g_new_window
            t = Timer()
            t.Interval = 300
            def go(s, ev):
                t.Stop()
                core.Navigate(GOOFISH)
            t.Tick += go
            t.Start()

        # --- приложение: ссылки «Открыть» из ленты открываем во вкладке Goofish
        def a_new_window(sender, e):
            e.Handled = True
            if "jp.mercari.com" in str(e.Uri):
                v = site_view("mercari")
                if v.CoreWebView2 is not None:
                    v.CoreWebView2.Navigate(e.Uri)
                else:
                    site_goto["mercari"] = str(e.Uri)
                show("mercari")
                return
            if "avito.ru/" in str(e.Uri):       # «Искать на Avito» с «Главной»: страницу открываете вы, Щебень её только читает
                v = site_view("avito")
                if v.CoreWebView2 is not None:
                    v.CoreWebView2.Navigate(e.Uri)
                else:
                    site_goto["avito"] = str(e.Uri)
                show("avito")
                return
            if gv.CoreWebView2 is not None:
                gv.CoreWebView2.Navigate(e.Uri)
            if ui["mode"] != "side":
                show("goofish")

        def a_message(s_, ev):      # кнопка «Похожее» в ленте: поиск Goofish — во вкладке Goofish (только goofish.com)
            try:
                url = str(json.loads(ev.TryGetWebMessageAsString()).get("open_goofish", ""))
            except Exception:
                return
            if url.startswith("https://www.goofish.com/") and gv.CoreWebView2 is not None:
                gv.CoreWebView2.Navigate(url)
                if ui["mode"] != "side":
                    show("goofish")

        def a_ready(sender, e):
            if not e.IsSuccess:
                say("Встроенный браузер (лента) не запустился:", e.InitializationException)
                return
            av.CoreWebView2.NewWindowRequested += a_new_window
            av.CoreWebView2.WebMessageReceived += a_message

            def a_going(s_, ev):        # ссылка «Лента» / «Обучение» на другой странице — открываем их постоянное окно
                from urllib.parse import urlparse
                u = urlparse(str(ev.Uri))
                r = u.path.strip("/")
                if FEED.startswith(f"{u.scheme}://{u.netloc}/") and r in KEEP and not u.query:
                    ev.Cancel = True
                    form.BeginInvoke(Action(lambda: show({"": "feed", "learn": "learn"}[r], r)))
            av.CoreWebView2.NavigationStarting += a_going

            # перешли по ссылке внутри страницы — подсвечиваем нужную вкладку на панели
            modes = {"": "feed", "home": "home", "catalog": "catalog", "prices": "prices", "learn": "learn", "status": "status", "deals": "deals"}

            def a_src(s, ev):
                r = app_route()
                ui["route"] = r
                if ui["mode"] not in ("goofish", "watchv", "side") and r in modes and ui["mode"] != modes[r]:
                    ui["mode"] = modes[r]
                    paint()
            av.CoreWebView2.SourceChanged += a_src
            ui["route"] = None
            app_nav(ui.get("want", ""))

        gv.CoreWebView2InitializationCompleted += g_ready
        av.CoreWebView2InitializationCompleted += a_ready

        # --- счётчик новых на кнопке «Лента»
        alerts = {"seq": None}

        def poll(s, e):
            def work():
                info = server_info()
                n = info.get("new_good", 0)
                ui_calls = []
                if info.get("market_due") and not mkt["on"]:
                    ui_calls.append(start_market)
                if info.get("watch_kick"):
                    ui_calls.append(kick_watch)
                if n != bar_state["badge"]:
                    bar_state["badge"] = n
                    ui_calls.append(bar.Invalidate)
                al = server_info(f"alerts?after={alerts['seq'] or 0}", 2)
                if al:
                    new = al.get("items") or []
                    if alerts["seq"] is not None and new:   # при запуске старые не показываем
                        ui_calls.append(lambda: show_alerts(new))   # Windows и Telegram — сводкой, см. flush_alerts
                    alerts["seq"] = al.get("seq", 0)
                    if watch["on"] and al.get("blocked", 0) > watch["since"]:
                        ui_calls.append(lambda: stop_watch("Goofish просит проверку («被挤爆啦»)", check=True))
                for f in ui_calls:
                    try:
                        form.BeginInvoke(Action(f))
                    except Exception:
                        pass
            threading.Thread(target=work, daemon=True).start()
        timer = Timer()
        timer.Interval = 3000
        timer.Tick += poll
        ntimer = Timer()
        ntimer.Interval = 5000
        ntimer.Tick += note_tick

        def on_load(s, e):
            gv.EnsureCoreWebView2Async(None)
            av.EnsureCoreWebView2Async(None)
            show("home", "home")       # при запуске — главный экран; Goofish грузится в фоне
            timer.Start()
            stimer.Start()
            ntimer.Start()

        def on_closed(s, e):
            timer.Stop()
            ntimer.Stop()
            wtimer.Stop()
            mtimer.Stop()
            stimer.Stop()
            tray.Visible = False
            tray.Dispose()
            if own_server:
                import goofish_parser_pro as gp
                gp.stop_listen()

        form.Load += on_load
        form.FormClosed += on_closed
        form.Controls.Add(split)
        form.Controls.Add(bar)
        Application.EnableVisualStyles()
        Application.Run(form)

    th = Thread(ThreadStart(ui_thread))
    th.SetApartmentState(ApartmentState.STA)
    th.Start()
    th.Join()


# ---------------------------------------------------------------- запасной режим: окна Chrome
def work_area() -> tuple[int, int, int, int]:
    class RECT(ctypes.Structure):
        _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long), ("r", ctypes.c_long), ("b", ctypes.c_long)]
    rc = RECT()
    ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(rc), 0)   # SPI_GETWORKAREA
    return rc.l, rc.t, rc.r - rc.l, rc.b - rc.t


def open_window(url: str, x: int, y: int, w: int, h: int):
    chrome = next((p for p in CHROME if p.is_file()), None)
    if not chrome:
        os.startfile(url)
        return
    subprocess.Popen([str(chrome), f"--app={url}", f"--window-position={x},{y}", f"--window-size={w},{h}"])


def open_windows(goofish: bool = True):
    x, y, w, h = work_area()
    if not goofish:
        open_window(FEED, x + w // 4, y, w // 2, h)
        return
    left = int(w * 0.58)
    open_window(GOOFISH, x, y, left, h)
    open_window(FEED, x + left, y, w - left, h)


def tray_icon():
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((2, 2, 62, 62), radius=14, fill=(36, 88, 74))
    try:
        font = ImageFont.truetype("segoeuib.ttf", 40)
    except OSError:
        font = ImageFont.load_default()
    d.text((31, 34), "S", fill="white", font=font, anchor="mm")
    d.ellipse((46, 8, 56, 18), fill=(226, 189, 85))
    return img


def run_chrome_mode(own_server: bool = False):
    if not own_server and server_alive():
        open_windows()
        return
    import pystray
    import goofish_parser_pro as gp

    if not own_server:
        start_server()
    open_windows()

    def quit_app(icon, _item):
        gp.stop_listen()
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("Открыть Goofish и ленту", lambda: open_windows(), default=True),
        pystray.MenuItem("Только лента", lambda: open_windows(goofish=False)),
        pystray.MenuItem("Журнал", lambda: os.startfile(LOG)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Выход", quit_app),
    )
    pystray.Icon("goofish", tray_icon(), "Sheben'Store", menu).run()


def first_copy() -> bool:
    """Вторая копия не нужна: две следили бы одновременно и удвоили бы запросы к Goofish.
    Окно уже открыто — показываем его; прежняя копия как раз закрывается — дожидаемся её."""
    import ctypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    u32 = ctypes.WinDLL("user32")
    k32.CreateMutexW.restype = ctypes.c_void_p
    k32.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
    h = k32.CreateMutexW(None, True, "Local\\ShebenStore")
    if ctypes.get_last_error() != 183:      # ERROR_ALREADY_EXISTS
        return True
    find = lambda: u32.FindWindowW(None, "Sheben'Store")
    if not find() and k32.WaitForSingleObject(h, 10000) in (0, 0x80):    # прежняя копия закрылась
        return True
    hwnd = find()
    if hwnd:
        if u32.IsIconic(hwnd):
            u32.ShowWindow(hwnd, 9)         # SW_RESTORE
        u32.SetForegroundWindow(hwnd)
    say("Щебень уже запущен — показал открытое окно.")
    return False


def main():
    if not first_copy():
        return
    if "--chrome" in sys.argv:
        run_chrome_mode()
        return
    own = start_server()
    try:
        run_window(own)
    except Exception as e:
        import traceback
        say("Окно приложения не открылось, запускаю запасной режим:", repr(e))
        traceback.print_exc()
        run_chrome_mode(own)


if __name__ == "__main__":
    main()
