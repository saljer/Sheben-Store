// Панель Щебня на странице товара во вкладке Goofish (только в app.pyw). Ничего не запрашивает у сайта и ничего
// на нём не нажимает: оценку присылает приложение, когда страница сама получила карточку товара.
(function () {
  if (!(window.chrome && window.chrome.webview)) return;
  let box = null, shown = "", card = null;
  let folded = false;
  try { folded = localStorage.getItem("sheben_panel_folded") === "1"; } catch (e) {}

  const fmt = (v) => Math.round(+v || 0).toLocaleString("ru-RU");
  const plural = (n, a, b, c) => { const m = n % 10, h = n % 100; return m === 1 && h !== 11 ? a : m >= 2 && m <= 4 && (h < 12 || h > 14) ? b : c; };
  const age = (d) => d >= 365 ? `${Math.floor(d / 365)} ${plural(Math.floor(d / 365), "год", "года", "лет")}` : `${d} ${plural(d, "день", "дня", "дней")}`;
  // страница товара (/item?id=) или профиль продавца (/personal?userId=); Mercari — /item/<id>, /shops/product/<id>
  const onPage = (c) => {
    if (location.hostname === "jp.mercari.com")
      return c.kind !== "seller" && [`/item/${c.id}`, `/shops/product/${c.id}`].includes(location.pathname);
    const p = new URLSearchParams(location.search);
    return c.kind === "seller" ? location.pathname === "/personal" && p.get("userId") === c.id
      : location.pathname === "/item" && p.get("id") === c.id;
  };

  function el(tag, css, text) {
    const e = document.createElement(tag);
    if (css) e.style.cssText = css;
    if (text != null) e.textContent = String(text);
    return e;
  }

  function line(parent, text, color) {
    if (!text) return;
    parent.appendChild(el("div", "margin-top:5px;" + (color ? "color:" + color : ""), text));
  }

  function draw() {
    if (!card || !onPage(card)) { if (box) box.style.display = "none"; return; }
    const appear = !box || box.style.display === "none";
    if (!box) {
      box = el("div", "position:fixed;right:16px;bottom:16px;z-index:2147483647;width:320px;max-width:calc(100vw - 32px);"
        + "background:#151a18;color:#e7ebe8;border:1px solid #2a312d;border-radius:10px;box-shadow:0 6px 24px rgba(0,0,0,.35);"
        + "font:13px/1.4 'Segoe UI',sans-serif;");
      document.documentElement.appendChild(box);
    }
    box.style.display = "block";
    box.replaceChildren();
    // появляется плавно: выезжает снизу (если в Windows не отключены анимации)
    if (appear && box.animate && !matchMedia("(prefers-reduced-motion: reduce)").matches)
      box.animate([{ opacity: 0, transform: "translateY(16px) scale(.97)" }, { opacity: 1, transform: "none" }],
        { duration: 260, easing: "cubic-bezier(.2,.9,.3,1.15)" });
    const c = card, s = c.seller || {};
    const head = el("div", "display:flex;align-items:center;gap:8px;padding:10px 12px;cursor:pointer;user-select:none");
    head.appendChild(el("b", "color:#86c4ae", "Щебень"));
    const seller = c.kind === "seller";
    const verdict = seller ? "продавец" : c.ok ? `${c.total} · ${c.tier || ""}` : "отсеяно";
    head.appendChild(el("span", "flex:1;color:" + (seller || c.ok ? (c.tier === "A" ? "#e2bd55" : "#e7ebe8") : "#929a95"), verdict));
    if (!seller && c.ok && +c.profit_rub) head.appendChild(el("span", "color:" + (+c.profit_rub > 0 ? "#86c4ae" : "#e07a6a"), `${fmt(c.profit_rub)} ₽`));
    head.appendChild(el("span", "color:#929a95", folded ? "▴" : "▾"));
    head.onclick = () => { folded = !folded; try { localStorage.setItem("sheben_panel_folded", folded ? "1" : "0"); } catch (e) {} draw(); };
    box.appendChild(head);
    if (folded) return;

    const body = el("div", "padding:0 12px 12px;border-top:1px solid #2a312d");
    if (seller) {
      line(body, `«${s.nick}»`);
      line(body, c.feed_n ? `В ленте его вещей: ${c.feed_n}, выгодных без риска подделки: ${c.feed_ok}`
        + (+c.best ? ` · лучшая прибыль ${fmt(c.best)} ₽` : "") : "В ленте его вещей пока нет", c.feed_ok ? "#86c4ae" : "#929a95");
    } else if (c.ok) {
      line(body, [c.brand, c.model, c.subtype || c.category].filter(Boolean).join(" · "));
      if (+c.sell_rub) line(body, `Продать ~${fmt(c.sell_rub)} ₽` + (c.best_channel ? ` (${c.best_channel})` : "")
        + (+c.profit_rub ? ` · прибыль ${fmt(c.profit_rub)} ₽` : ""));
      const jp = c.site === "mercari";     // Mercari: цены в иенах, затраты — посредник и доставка из Японии
      if (jp && +c.max_buy_jpy) line(body, `Брать до ¥${fmt(c.max_buy_jpy)} · сейчас ¥${fmt(c.price_jpy)} (иены, через посредника)`,
        +c.price_jpy <= +c.max_buy_jpy ? "#86c4ae" : "#e07a6a");
      else if (+c.max_buy_cny) line(body, `Брать до ¥${fmt(c.max_buy_cny)} · сейчас ¥${fmt(c.price_cny)}`,
        +c.price_cny <= +c.max_buy_cny ? "#86c4ae" : "#e07a6a");
      if (c.deal_pct) line(body, `Дешевле рынка ${c.site === "mercari" ? "Mercari" : "Goofish"} на ${c.deal_pct}%`, "#e2bd55");
      // цены России (каналы, Avito, Oskelly) и насколько с ними расходится каталог
      if (c.ru_mkt) line(body, `Россия: ${c.ru_mkt}` + (c.ru_diff !== "" && c.ru_diff != null
        ? ` · каталог ${c.ru_diff > 0 ? "выше" : "ниже"} рынка на ${Math.abs(c.ru_diff)}%` : ""), c.ru_diff !== "" && c.ru_diff != null ? "#e2bd55" : "#86c4ae");
      if (c.condition) line(body, `Состояние: ${c.condition}`);
      const risk = { "высокий": "#e07a6a", "средний": "#e2bd55" }[c.fake_risk];
      if (c.fake_risk) line(body, `Риск подделки: ${c.fake_risk}` + (c.risk_why ? ` — ${c.risk_why}` : ""), risk || "#929a95");
      if (c.outside) line(body, `Вне каталога: ${c.outside}` + (c.verdict_why ? ` — ${c.verdict_why}` : ""), "#929a95");
      if (c.mark === "like") line(body, "★ отмечено вами", "#e2bd55");
    } else {
      line(body, c.hidden ? `Скрыта: ${c.reject}` : `Не прошло фильтры: ${c.reject}`, "#929a95");
      if (c.hidden && c.item_only) line(body, "Похожие вещи это не скрывает — скрыта только эта", "#929a95");
      if (c.hidden) {       // ваш ✕ можно отменить прямо здесь — вещь снова появится в ленте
        const un = el("button", "margin-top:8px;padding:5px 10px;border-radius:6px;cursor:pointer;font:inherit;"
          + "background:transparent;color:#86c4ae;border:1px solid #86c4ae", "Вернуть в ленту");
        un.onclick = () => { un.disabled = true; un.textContent = "возвращаю…";
          window.chrome.webview.postMessage(JSON.stringify({ unhide: c.id })); };
        body.appendChild(un);
      }
    }
    if (s.uid) {
      const sec = el("div", seller ? "" : "margin-top:9px;padding-top:8px;border-top:1px solid #2a312d");
      if (!seller) line(sec, `Продавец «${s.nick}»`);
      else if (c.intro) line(sec, c.intro, "#929a95");
      if (s.days != null) line(sec, [s.days ? `на ${s.site === "mercari" ? "Mercari" : "闲鱼"} ${age(s.days)}` : "", s.sold ? `продал ${s.sold}` : "",
        `отзывы +${s.good || 0} / −${s.bad || 0}`, s.visit].filter(Boolean).join(" · "),
        (s.bad > 0 && s.bad * 10 > s.good) || s.days < 90 ? "#e2bd55" : "#929a95");
      if (c.blacklist) line(sec, `Продавец реплик: ${c.blacklist}`, "#e07a6a");
      const btn = el("button", "margin-top:8px;padding:5px 10px;border-radius:6px;cursor:pointer;font:inherit;"
        + (c.followed ? "background:transparent;color:#929a95;border:1px solid #2a312d" : "background:#86c4ae;color:#0d1210;border:0"),
        c.followed ? "Вы следите · убрать" : "Следить за продавцом");
      btn.onclick = () => {
        c.followed = !c.followed;
        if (!c.followed) c.every = false;
        window.chrome.webview.postMessage(JSON.stringify({ follow: { uid: s.uid, nick: s.nick, on: c.followed, site: s.site || "" } }));
        draw();
      };
      sec.appendChild(btn);
      if (c.followed) {     // обычно профиль смотрится раз в 3 круга; лучших — каждый круг
        const ev = el("label", "display:inline-flex;align-items:center;gap:5px;margin-left:10px;cursor:pointer;color:#929a95");
        const cb = el("input");
        cb.type = "checkbox";
        cb.checked = !!c.every;
        cb.onchange = () => {
          c.every = cb.checked;
          window.chrome.webview.postMessage(JSON.stringify({ follow: { uid: s.uid, nick: s.nick, on: true, every: c.every, site: s.site || "" } }));
          draw();
        };
        ev.appendChild(cb);
        ev.appendChild(el("span", "", "каждый круг"));
        sec.appendChild(ev);
      }
      body.appendChild(sec);
    }
    box.appendChild(body);
  }

  window.chrome.webview.addEventListener("message", (e) => {
    const d = e.data || {};
    if (d.sheben_card) { card = d.sheben_card; draw(); }
    if (d.sheben_follow && card && card.seller && card.seller.uid === d.sheben_follow.uid) {
      card.followed = d.sheben_follow.followed; card.every = d.sheben_follow.every; draw();
    }
  });
  // Goofish меняет страницу без перезагрузки: ушли с этой вещи или профиля — панель прячем
  setInterval(() => { if (box && box.style.display !== "none" && (!card || !onPage(card))) box.style.display = "none"; }, 800);

  // кнопка «наверх»: после двух экранов прокрутки; над панелью Щебня, если она открыта. Только прокручивает страницу
  let top = null;
  function placeTop() {
    if (!document.body) return;
    if (!top) {
      top = el("button", "position:fixed;right:22px;z-index:2147483646;width:46px;height:46px;border-radius:50%;cursor:pointer;"
        + "border:1px solid rgba(134,196,174,.6);background:rgba(21,26,24,.82);color:#86c4ae;font:600 20px/1 system-ui,sans-serif;"
        + "box-shadow:0 8px 24px rgba(0,0,0,.3);transition:opacity .25s,transform .25s;opacity:0;transform:translateY(14px) scale(.9);"
        + "pointer-events:none", "↑");
      top.title = "Наверх";
      top.onclick = () => scrollTo({ top: 0, behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
      document.body.appendChild(top);
    }
    const show = scrollY > 2 * innerHeight;
    top.style.opacity = show ? "1" : "0";
    top.style.transform = show ? "none" : "translateY(14px) scale(.9)";
    top.style.pointerEvents = show ? "auto" : "none";
    top.style.bottom = (box && box.style.display !== "none" ? box.offsetHeight + 30 : 22) + "px";
  }
  addEventListener("scroll", placeTop, { passive: true });
  addEventListener("resize", placeTop);
  setInterval(placeTop, 1000);
})();
