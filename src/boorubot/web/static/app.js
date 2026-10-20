/* boorubot · interações do painel
   Componentes do design system (templates/_ui.html): toasts, confirmação em <dialog> nativo, menus.
   Shoelace (gaveta, abas) e ninja-keys (busca Ctrl+K) vêm por CDN e têm plano B: se não carregarem,
   o painel continua funcionando (confirm() nativo, guia em página única, menu sem gaveta). */
(function () {
  "use strict";

  const html = document.documentElement;
  const defined = (tag) => Boolean(customElements.get(tag));
  const whenDefined = (tag, ms = 3500) =>
    Promise.race([customElements.whenDefined(tag).then(() => true), new Promise((r) => setTimeout(() => r(false), ms))]);
  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const PET = (name) => `/static/pets/${name}.svg`;
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

  // Shoelace pronto (ou desistiu): libera a página. Evita "piscar" componentes sem estilo.
  whenDefined("sl-tab-group", 2500).then(() => html.classList.remove("sl-loading"));
  whenDefined("ninja-keys", 4000).then((ok) => html.classList.toggle("no-search", !ok));

  // ---------------------------------------------------------------- toasts
  // Toast escuro do design system: sucesso (✓), neutro (com "desfazer") ou erro (×), com uma ação opcional.
  const toastRegion = () => document.getElementById("toasts") || document.body;
  function armToast(el, duration) {
    let timer = setTimeout(() => el.remove(), duration);
    // com o foco ou o mouse no aviso (ex.: indo clicar em "desfazer"), ele espera
    el.addEventListener("pointerenter", () => clearTimeout(timer));
    el.addEventListener("focusin", () => clearTimeout(timer));
    el.addEventListener("pointerleave", () => { timer = setTimeout(() => el.remove(), 4000); });
    const close = el.querySelector("[data-toast-close]");
    if (close) close.addEventListener("click", () => el.remove());
  }
  function toast(message, { kind = "neutro", action = "", href = "", duration = 6000 } = {}) {
    const el = document.createElement("div");
    el.className = "bb-toast"; el.setAttribute("role", "status"); el.dataset.toast = "";
    const mark = kind === "sucesso" ? '<span class="bb-toast__icon bb-toast__icon--ok" aria-hidden="true">✓</span>'
      : kind === "erro" ? '<span class="bb-toast__icon bb-toast__icon--err" aria-hidden="true">×</span>' : "";
    el.innerHTML = `${mark}<span class="bb-toast__msg">${esc(message)}</span>`
      + (action && href ? `<a class="bb-toast__action" href="${esc(href)}">${esc(action)}</a>` : "")
      + '<button type="button" class="bb-toast__close" data-toast-close aria-label="Fechar aviso">'
      + '<svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" aria-hidden="true"><path d="M5 5l10 10M15 5L5 15"/></svg></button>';
    toastRegion().append(el);
    armToast(el, duration);
    return el;
  }
  window.boorubotToast = toast;

  // recado deixado antes de recarregar a página (ex.: tarefa terminou)
  try {
    const pending = sessionStorage.getItem("boorubot-toast");
    if (pending) {
      sessionStorage.removeItem("boorubot-toast");
      const { message, kind } = JSON.parse(pending);
      toast(message, { kind: kind || "sucesso" });
    }
  } catch (e) {}

  // ----------------------------------------- aviso (?aviso=...) por ~8 s
  // Vem pronto do servidor (base.html). Some sozinho e sai da URL (recarregar não repete o aviso nem o "desfazer").
  const flash = document.querySelector("#toasts [data-toast]");
  if (flash) {
    const url = new URL(location.href);
    url.searchParams.delete("aviso"); url.searchParams.delete("desfazer"); url.searchParams.delete("tipo");
    history.replaceState(null, "", url.pathname + url.search + url.hash);
    armToast(flash, 8000);
  }

  // ------------------------------------------------- tarefas em andamento
  // Igual a antes: consulta /status.json e recarrega quando tudo termina. Agora avisa com um toast.
  const jobsEl = document.getElementById("jobs");
  if (jobsEl) {
    let busy = Number(jobsEl.dataset.count) > 0;
    let lastLabel = "";
    setInterval(async () => {
      try {
        const { jobs } = await (await fetch("/status.json")).json();
        if (jobs.length) lastLabel = jobs[0].replace(" (rodando)", "");
        jobsEl.innerHTML = jobs.length
          ? '<span class="spin"></span> ' + esc(jobs[0]) + (jobs.length > 1 ? " (+" + (jobs.length - 1) + ")" : "")
          : "";
        if (busy && !jobs.length) {
          try {
            sessionStorage.setItem("boorubot-toast", JSON.stringify({ message: `Pronto: ${lastLabel || "tarefa concluída"}`, kind: "sucesso" }));
          } catch (e) {}
          location.reload();
        }
        busy = jobs.length > 0;
      } catch (e) {}
    }, 3000);
  }

  // ------------------------------------------------------ confirmações
  // <dialog> nativo (ConfirmDialog): showModal() prende o foco, Esc fecha e o foco volta ao botão de origem.
  // Um botão com data-confirm="texto" usa o diálogo genérico de base.html; data-confirm-title e
  // data-confirm-ok trocam o título e o rótulo da ação (padrão: o texto do próprio botão).
  function openDialog(dialog) {
    return new Promise((resolve) => {
      const ok = dialog.querySelector("[data-dialog-ok]");
      const cancel = dialog.querySelector("[data-dialog-cancel]");
      const onOk = () => dialog.close("ok");
      const onCancel = () => dialog.close("");
      const onClose = () => {
        ok && ok.removeEventListener("click", onOk); cancel && cancel.removeEventListener("click", onCancel);
        dialog.removeEventListener("close", onClose);
        resolve(dialog.returnValue === "ok");
      };
      ok && ok.addEventListener("click", onOk); cancel && cancel.addEventListener("click", onCancel);
      dialog.addEventListener("close", onClose);
      dialog.returnValue = "";
      dialog.showModal();
      (cancel || ok).focus();
    });
  }
  window.boorubotDialog = openDialog;

  function askConfirm(btn) {
    const dialog = document.getElementById("confirm-dialog");
    const text = btn.dataset.confirm;
    if (!dialog || !dialog.showModal) return Promise.resolve(window.confirm(text));
    dialog.querySelector("[data-dialog-title]").textContent = btn.dataset.confirmTitle || "Tem certeza?";
    dialog.querySelector("[data-dialog-text]").textContent = text;
    const ok = dialog.querySelector("[data-dialog-ok]");
    ok.textContent = btn.dataset.confirmOk || btn.textContent.trim() || "Confirmar";
    ok.className = "bb-btn " + ("confirmDanger" in btn.dataset ? "bb-btn--danger" : "bb-btn--primary");
    return openDialog(dialog);
  }

  document.addEventListener("click", (ev) => {
    const opener = ev.target.closest("[data-dialog-open]");
    if (opener) {
      const dialog = document.getElementById(opener.dataset.dialogOpen);
      if (dialog && dialog.showModal) { ev.preventDefault(); openDialog(dialog); }
      return;
    }
    const btn = ev.target.closest("[data-confirm]");
    if (!btn) return;
    if (btn.dataset.confirmed) { delete btn.dataset.confirmed; return; }
    ev.preventDefault();
    askConfirm(btn).then((ok) => {
      if (!ok) return;
      btn.dataset.confirmed = "1";
      if (btn.form && btn.form.requestSubmit) btn.form.requestSubmit(btn); else btn.click();
    });
  }, true);

  // --------------------------------------------------- menu no celular
  document.querySelectorAll("[data-open-menu]").forEach((b) => b.addEventListener("click", () => {
    const drawer = document.getElementById("menu-drawer");
    if (drawer && defined("sl-drawer")) drawer.show(); else html.classList.toggle("menu-open");
  }));

  // ----------------------------------------------------- busca Ctrl+K
  const SITES = { danbooru: "Danbooru", gelbooru: "Gelbooru", paheal: "rule34.paheal", rule34us: "rule34.us", rule34xxx: "rule34.xxx" };
  const GUIDE = [
    ["antes", "Antes de começar"], ["pc-acordado", "O PC precisa estar acordado"], ["danbooru", "Conectar o Danbooru"],
    ["gelbooru", "Conectar Gelbooru, paheal e rule34.us"], ["rule34xxx", "Conectar o rule34.xxx"], ["teste", "Testar um post"],
    ["valendo", "Começar a postar de verdade"], ["dia-a-dia", "Dia a dia"], ["regras", "Regras dos sites"],
    ["seguranca", "Segurança das suas contas"], ["problemas", "Algo deu errado?"],
  ];
  // os resultados da busca ficam dentro do shadow DOM do ninja-keys: o CSS da página não chega lá,
  // então o tamanho vai inline (senão a miniatura da arte aparece no tamanho original)
  const ICON_STYLE = "width:28px;height:28px;object-fit:cover;border-radius:8px;margin-right:8px;flex:none";
  const icon = (pet) => `<img class="nk-pet" src="${PET(pet)}" alt="" style="${ICON_STYLE}">`;
  const go = (url) => () => { location.href = url; };
  function post(url) {
    return () => {
      const f = Object.assign(document.createElement("form"), { method: "post", action: url });
      document.body.append(f); f.submit();
    };
  }
  function newArt() {
    const opener = document.querySelector("[data-new-art]");
    if (opener) opener.click(); else location.href = "/";
  }

  function baseActions() {
    const actions = [
      { id: "nova-arte", title: "Subir nova arte", section: "Ações", icon: icon("puppy"), keywords: "upload imagem nova", handler: newArt },
      { id: "metricas", title: "Atualizar métricas do Danbooru", section: "Ações", icon: icon("bear"), keywords: "score favoritos", handler: post("/metricas/atualizar") },
      { id: "pg-fila", title: "Início", section: "Páginas", icon: icon("cat"), keywords: "fila painel do dia rascunhos agendados", handler: go("/") },
      { id: "pg-calendario", title: "Calendário", section: "Páginas", icon: icon("bunny"), keywords: "agenda semana ritmo por site", handler: go("/calendario") },
      { id: "cfg-ritmo", title: "Ritmo por site", section: "Páginas", icon: icon("bunny"), keywords: "dia sim dia não atraso desligar site", handler: go("/sites#ritmo") },
      { id: "pg-metricas", title: "Métricas", section: "Páginas", icon: icon("bear"), keywords: "favoritos score gráfico ranking", handler: go("/metricas") },
      { id: "pg-sites", title: "Sites e configurações", section: "Páginas", icon: icon("puppy"), keywords: "contas modo teste horario backup", handler: go("/sites") },
      { id: "pg-artes", title: "Artes", section: "Páginas", icon: icon("paw"), keywords: "histórico publicadas envios rascunhos", handler: go("/artes") },
      { id: "pg-guia", title: "Guia", section: "Páginas", icon: icon("chick"), keywords: "ajuda tutorial", handler: go("/guia") },
      { id: "cfg-teste", title: "Modo teste e horário do post", section: "Páginas", icon: icon("chick"), keywords: "configurações tags padrão", handler: go("/sites#config") },
    ];
    GUIDE.forEach(([id, title]) => actions.push({ id: "guia-" + id, title, section: "Guia", icon: icon("chick"), keywords: "guia ajuda " + id, handler: go("/guia#" + id) }));
    Object.entries(SITES).forEach(([key, label]) => {
      actions.push({ id: "testar-" + key, title: `Testar conexão: ${label}`, section: "Sites", icon: icon("bunny"), keywords: "sessão login checar", handler: post(`/sites/${key}/session`) });
      if (key !== "danbooru") actions.push({ id: "entrar-" + key, title: `Entrar no ${label}`, section: "Sites", icon: icon("bunny"), keywords: "login captcha", handler: post(`/sites/${key}/login`) });
    });
    return actions;
  }

  whenDefined("ninja-keys", 4000).then(async (ok) => {
    if (!ok) return;
    const ninja = document.querySelector("ninja-keys");
    ninja.data = baseActions();
    document.querySelectorAll("[data-open-search]").forEach((b) => b.addEventListener("click", () => ninja.open()));
    try {
      const { posts } = await (await fetch("/busca.json")).json();
      const arts = posts.map((p) => ({
        id: "arte-" + p.id,
        title: `${p.title} · ${p.status_label}`,
        section: "Artes",
        icon: `<img class="nk-thumb" src="/images/${encodeURIComponent(p.image)}" alt="" style="${ICON_STYLE}">`,
        keywords: `${p.tags} ${p.status} #${p.id}`,
        handler: go(`/posts/${p.id}`),
      }));
      ninja.data = [...arts, ...baseActions()];
    } catch (e) {}
  });

  // ------------------------------------------------------ guia em abas
  const tabs = document.querySelector(".guide-tabs");
  if (tabs) {
    whenDefined("sl-tab-group").then((ok) => {
      if (!ok) return;
      const names = [...tabs.querySelectorAll("sl-tab-panel")].map((p) => p.getAttribute("name"));
      const fromHash = () => decodeURIComponent(location.hash.slice(1));
      const small = matchMedia("(max-width: 860px)");
      const place = () => tabs.setAttribute("placement", small.matches ? "top" : "start");
      place(); small.addEventListener("change", place);
      const open = (name, scroll) => {
        if (!names.includes(name)) return false;
        tabs.show(name);
        if (scroll) document.querySelector(".guide").scrollIntoView({ behavior: reducedMotion ? "auto" : "smooth", block: "start" });
        return true;
      };
      open(fromHash(), false);
      tabs.addEventListener("sl-tab-show", (ev) => {
        history.replaceState(null, "", "#" + ev.detail.name);
        const panel = tabs.querySelector(`sl-tab-panel[name="${ev.detail.name}"]`);
        if (panel && !reducedMotion) panel.animate([{ opacity: 0, transform: "translateY(10px)" }, { opacity: 1, transform: "none" }], { duration: 260, easing: "cubic-bezier(.2,.8,.2,1)" });
      });
      document.addEventListener("click", (ev) => {
        const a = ev.target.closest('a[href^="#"]');
        if (a && open(a.getAttribute("href").slice(1), true)) ev.preventDefault();
      });
      window.addEventListener("hashchange", () => open(fromHash(), true));
    });
  }

  // ------------------------------------------------------- animações
  if (!reducedMotion) {
    // entrada em cascata do conteúdo da página
    // o editor fica de fora: transform no pai prenderia a barra de ações fixa dentro dele
    const items = document.querySelectorAll("main > *:not(.editor), .grid > .thumb, .site-list > .card");
    items.forEach((el, i) => { el.classList.add("reveal"); el.style.setProperty("--i", Math.min(i, 14)); });

  }

})();
