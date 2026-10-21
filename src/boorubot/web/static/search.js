/* boorubot · busca Ctrl+K (frame "Modais e avisos")
   Diálogo próprio: Artes (miniatura + situação), Ações, Páginas e Guia, com ↑ ↓ Enter Esc.
   O filtro roda aqui, em cima de /busca.json. Sem JavaScript, o botão Buscar leva para Artes, que tem busca. */
(function () {
  "use strict";

  const dialog = document.getElementById("search");
  if (!dialog || typeof dialog.showModal !== "function") return;
  const input = dialog.querySelector("input");
  const list = dialog.querySelector("[data-search-results]");
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const ICON = (path) => `<svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${path}</svg>`;
  const PLUS = ICON('<path d="M10 4v12M4 10h12"/>');
  const PAGE = ICON('<path d="M6 3h8a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1zM8 7h4M8 10h4"/>');
  const GO = ICON('<path d="M8 5l5 5-5 5"/>');

  const go = (url) => () => { location.href = url; };
  const post = (url) => () => {
    const f = Object.assign(document.createElement("form"), { method: "post", action: url });
    document.body.append(f); f.submit();
  };
  const SITES = { danbooru: "Danbooru", paheal: "rule34.paheal", rule34xxx: "rule34.xxx" };
  const ACTIONS = [
    { title: "Nova arte", keys: "subir upload imagem", icon: PLUS, run: () => { dialog.close(); document.querySelector("[data-new-art]")?.click(); } },
    { title: "Atualizar métricas", keys: "favoritos score danbooru", icon: GO, run: post("/metricas/atualizar") },
    ...Object.entries(SITES).flatMap(([key, label]) => [
      { title: `Testar conexão: ${label}`, keys: "sessão login checar site", icon: GO, run: post(`/sites/${key}/session`) },
      ...(key === "danbooru" ? [] : [{ title: `Entrar no ${label}`, keys: "login captcha site", icon: GO, run: post(`/sites/${key}/login`) }]),
    ]),
  ];
  const PAGES = [
    ["Início", "/", "painel do dia hoje"], ["Abrir Artes", "/artes", "histórico publicadas rascunhos fila"],
    ["Calendário", "/calendario", "semana ritmo por site agenda"], ["Métricas", "/metricas", "favoritos score gráfico"],
    ["Sites", "/sites", "contas login danbooru chave"], ["Ajustes", "/ajustes", "modo teste horário backup tags padrão windows"],
  ].map(([title, url, keys]) => ({ title, keys, icon: PAGE, run: go(url) }));
  const GUIDE = [
    ["antes", "Antes de começar"], ["pc-acordado", "O PC precisa estar acordado"], ["danbooru", "Conectar o Danbooru"],
    ["paheal", "Conectar o paheal"], ["rule34xxx", "Enviar à mão no rule34.xxx"], ["teste", "Testar um post"],
    ["valendo", "Começar a postar de verdade"], ["dia-a-dia", "O dia a dia"], ["regras", "Regras dos sites"],
    ["paheal", "Tags e OC no paheal"], ["seguranca", "Segurança das contas"], ["problemas", "Algo deu errado?"],
  ].map(([id, title]) => ({ title: "Guia: " + title, keys: "ajuda " + id, icon: PAGE, run: go(`/guia?passo=${id}#${id}`) }));

  let arts = [];
  let loaded = false;
  let items = [];
  let active = 0;

  const norm = (s) => String(s).toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
  const matches = (q, text) => q.split(/\s+/).every((w) => norm(text).includes(w));

  function render() {
    const q = norm(input.value.trim());
    const found = (src, max) => (q ? src.filter((it) => matches(q, it.title + " " + (it.keys || ""))) : src).slice(0, max);
    const groups = [
      ["Artes", found(arts, q ? 6 : 4)],
      ["Ações", found(ACTIONS, q ? 6 : 2)],
      ["Páginas", found(PAGES, q ? 6 : 6)],
      ["Guia", found(GUIDE, q ? 4 : 0)],
    ].filter(([, its]) => its.length);
    items = groups.flatMap(([, its]) => its);
    active = Math.min(active, Math.max(items.length - 1, 0));
    let n = 0;
    list.innerHTML = groups.map(([name, its]) => `<p class="bb-search__group" role="presentation">${name}</p>` + its.map((it) => {
      const i = n++;
      const lead = it.image ? `<img src="/images/${encodeURIComponent(it.image)}" alt="">` : `<span class="bb-search__icon">${it.icon}</span>`;
      const sub = it.detail ? `<small>${esc(it.detail)}</small>` : "";
      return `<div class="bb-search__item" role="option" id="sr-${i}" data-i="${i}" aria-selected="${i === active}">${lead}<span><b>${esc(it.title)}</b>${sub}</span>${i === active ? '<kbd class="bb-kbd">Enter</kbd>' : ""}</div>`;
    }).join("")).join("") || `<p class="bb-search__empty">Nada encontrado para "${esc(input.value)}".</p>`;
    input.setAttribute("aria-activedescendant", items.length ? `sr-${active}` : "");
    list.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" });
  }

  async function open() {
    if (!dialog.open) dialog.showModal();
    input.value = ""; active = 0; render(); input.focus();
    if (loaded) return;
    try {
      const { posts } = await (await fetch("/busca.json")).json();
      arts = posts.map((p) => ({ title: p.title, keys: `${p.tags} ${p.status_label} #${p.id}`, image: p.image,
                                 detail: p.detail || p.status_label, run: go(`/posts/${p.id}`) }));
      loaded = true; render();
    } catch (e) {}
  }

  input.addEventListener("input", () => { active = 0; render(); });
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
      ev.preventDefault();
      active = (active + (ev.key === "ArrowDown" ? 1 : -1) + items.length) % Math.max(items.length, 1);
      render();
    } else if (ev.key === "Enter" && items[active]) {
      ev.preventDefault();
      items[active].run();
    }
  });
  list.addEventListener("mousemove", (ev) => {
    const el = ev.target.closest("[data-i]");
    if (el && Number(el.dataset.i) !== active) { active = Number(el.dataset.i); render(); }
  });
  list.addEventListener("click", (ev) => {
    const el = ev.target.closest("[data-i]");
    if (el) items[Number(el.dataset.i)].run();
  });
  dialog.addEventListener("click", (ev) => { if (ev.target === dialog) dialog.close(); });   // clique no véu fecha

  document.addEventListener("click", (ev) => {
    const btn = ev.target.closest("[data-open-search]");
    if (!btn) return;
    ev.preventDefault();
    open();
  });
  document.addEventListener("keydown", (ev) => {
    if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "k") { ev.preventDefault(); open(); }
  });
  window.boorubotSearch = { open };
})();
