// Работает внутри страницы Goofish. Ничего не запрашивает сам: только читает копии ответов
// поиска и карточек, которые страница уже получила, пока вы ею пользуетесь.
(function () {
  // + Mercari (Япония): выдача и карточка, которые страница jp.mercari.com сама получила
  const MARKERS = ["mtop.taobao.idlemtopsearch.pc.search", "mtop.taobao.idle.pc.detail",
                   "api.mercari.jp/v2/entities:search", "api.mercari.jp/items/get"];
  // лента «猜你喜欢» на главной и рекомендации под карточкой: точное имя API у сайта меняется
  // + список вещей в профиле продавца (когда вы сами открываете профиль) и ваш список подписок (关注)
  const FEED = ["idlehome", "feed", "recommend", "guess", "xyh", "item.list", "personal", "user.page",
                "follow", "attention", "fans"];
  const wanted = (url) => {
    const u = String(url || "");
    return MARKERS.some((m) => u.includes(m)) || (u.includes("mtop.") && FEED.some((m) => u.toLowerCase().includes(m)));
  };

  // имена прочих запросов mtop (без содержимого): чтобы найти, каким запросом сайт отдаёт новый список
  const seenApi = new Set();
  function probe(url) {
    const m = String(url || "").match(/\/h5\/([^/]+)\//);
    if (!m || seenApi.has(m[1])) return;
    seenApi.add(m[1]);
    send(url, { probe: 1 });
  }

  function send(url, data) {
    window.postMessage({ __goofishParser: true, apiUrl: String(url), pageUrl: location.href, data: data }, location.origin);
  }

  function forward(url, text) {
    let data;
    try {
      data = JSON.parse(text);
    } catch (e) {
      return;
    }
    send(url, data);
  }

  const origFetch = window.fetch;
  window.fetch = function (input, init) {
    const url = typeof input === "string" ? input : (input && input.url) || "";
    const p = origFetch.apply(this, arguments);
    if (wanted(url)) {
      p.then((resp) => resp.clone().text().then((t) => forward(url, t))).catch(() => {});
    } else if (String(url).includes("mtop.")) {
      probe(url);
    }
    return p;
  };

  const origOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (method, url) {
    this.__goofishUrl = url;
    return origOpen.apply(this, arguments);
  };
  const origSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.send = function () {
    if (wanted(this.__goofishUrl)) {
      this.addEventListener("load", () => {
        try {
          const t = this.responseType === "" || this.responseType === "text"
            ? this.responseText
            : JSON.stringify(this.response);
          forward(this.__goofishUrl, t);
        } catch (e) {}
      });
    } else if (String(this.__goofishUrl || "").includes("mtop.")) {
      probe(this.__goofishUrl);
    }
    return origSend.apply(this, arguments);
  };
})();
