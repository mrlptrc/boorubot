/* boorubot · Sites
   - painel lateral aberto pela URL (?painel=site, plano B sem JavaScript) vira gaveta modal;
   - "esperando login": acompanha /sites/status.json e atualiza a página quando a sessão muda. */
(function () {
  "use strict";

  // espera o app.js (que também é defer, mas vem depois na página) expor o boorubotDialog
  document.addEventListener("DOMContentLoaded", () => {
    const open = document.querySelector("dialog.bb-drawer[open]");
    if (open && open.showModal && window.boorubotDialog) {
      open.close();
      window.boorubotDialog(open);
    }
  });

  const snapshot = (rows) => rows.map((r) => r.site + ":" + r.state).join(",");
  const initial = [...document.querySelectorAll("[data-site-row]")]
    .map((li) => li.dataset.siteRow + ":" + (li.className.match(/site-card--(\w+)/) || [])[1]).join(",");
  if (!document.querySelector(".site-card--login")) return;
  const timer = setInterval(async () => {
    try {
      const { rows } = await (await fetch("/sites/status.json")).json();
      if (snapshot(rows) !== initial) { clearInterval(timer); location.reload(); }
    } catch (e) {}
  }, 2500);
})();
