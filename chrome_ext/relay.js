// Передаёт перехваченные ответы из страницы в фоновый скрипт расширения.
window.addEventListener("message", (ev) => {
  if (ev.source !== window || !ev.data || ev.data.__goofishParser !== true) return;
  const { apiUrl, pageUrl, data } = ev.data;
  try {
    chrome.runtime.sendMessage({ type: "goofish", apiUrl, pageUrl, data });
  } catch (e) {} // расширение перезагружено: обновите вкладку
});
