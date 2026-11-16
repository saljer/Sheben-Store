// Отправляет данные в goofish_parser_pro.py --listen (только на этот компьютер, 127.0.0.1).
// Значок: число переданных объявлений; «off» = скрипт не запущен; «!» = сайт сообщил об ограничении.
const ENDPOINT = "http://127.0.0.1:8765/items";
let sent = 0;

// Щелчок по значку открывает живой отчёт скрипта.
chrome.action.onClicked.addListener(() => chrome.tabs.create({ url: "http://127.0.0.1:8765/" }));

function badge(text, color) {
  chrome.action.setBadgeText({ text });
  chrome.action.setBadgeBackgroundColor({ color });
}

chrome.runtime.onMessage.addListener((msg) => {
  if (!msg || msg.type !== "goofish") return;
  fetch(ENDPOINT, {
    method: "POST",
    // свой заголовок: обычные сайты не смогут слать данные в ваш скрипт
    headers: { "Content-Type": "application/json", "X-Goofish-Ext": "1" },
    body: JSON.stringify({ apiUrl: msg.apiUrl, pageUrl: msg.pageUrl, data: msg.data }),
  })
    .then((r) => r.json())
    .then((res) => {
      if (res.blocked) {
        badge("!", "#c62828");
        return;
      }
      sent += res.added || 0;
      badge(String(sent), "#2e7d32");
    })
    .catch(() => badge("off", "#757575"));
});
