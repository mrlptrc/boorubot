/* boorubot · interações do painel
   Componentes: Shoelace (gaveta, diálogo, toasts, abas) e ninja-keys (busca Ctrl+K), por CDN.
   Tudo aqui tem plano B: se uma lib não carregar (sem internet, CDN fora), o painel continua
   funcionando como antes (confirm() nativo, guia em página única, menu sem gaveta). */
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
  function toast(message, { pet = "cat", variant = "primary", duration = 4500 } = {}) {
    if (defined("sl-alert")) {
      const alert = Object.assign(document.createElement("sl-alert"), { variant, closable: true, duration });
      alert.className = "cute-toast";
      alert.innerHTML = `<img slot="icon" src="${PET(pet)}" alt=""><span>${esc(message)}</span>`;
      document.body.append(alert);
      return alert.toast();
    }
    const el = document.createElement("div");
    el.className = "toast-fallback";
    el.innerHTML = `<img src="${PET(pet)}" alt=""><span>${esc(message)}</span>`;
    document.body.append(el);
    setTimeout(() => el.classList.add("out"), duration);
    setTimeout(() => el.remove(), duration + 400);
  }
  window.boorubotToast = toast;

  // recado deixado antes de recarregar a página (ex.: tarefa terminou)
  try {
    const pending = sessionStorage.getItem("boorubot-toast");
    if (pending) {
      sessionStorage.removeItem("boorubot-toast");
      const { message, pet } = JSON.parse(pending);
      whenDefined("sl-alert", 2000).then(() => toast(message, { pet }));
    }
  } catch (e) {}

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
            sessionStorage.setItem("boorubot-toast", JSON.stringify({ message: `Pronto: ${lastLabel || "tarefa concluída"} ♡`, pet: "cat" }));
          } catch (e) {}
          location.reload();
        }
        busy = jobs.length > 0;
      } catch (e) {}
    }, 3000);
  }

  // ------------------------------------------------ confirmações fofas
  function askConfirm(text) {
    const dialog = document.getElementById("confirm-dialog");
    if (!dialog || !defined("sl-dialog")) return Promise.resolve(window.confirm(text));
    dialog.querySelector("[data-confirm-text]").textContent = text;
    return new Promise((resolve) => {
      let answer = false;
      const yes = dialog.querySelector("[data-confirm-yes]");
      const no = dialog.querySelector("[data-confirm-no]");
      const onYes = () => { answer = true; dialog.hide(); };
      const onNo = () => dialog.hide();
      const onHide = (ev) => {
        if (ev.target !== dialog) return;
        yes.removeEventListener("click", onYes); no.removeEventListener("click", onNo);
        dialog.removeEventListener("sl-after-hide", onHide);
        resolve(answer);
      };
      yes.addEventListener("click", onYes); no.addEventListener("click", onNo);
      dialog.addEventListener("sl-after-hide", onHide);
      dialog.show();
    });
  }

  document.addEventListener("click", (ev) => {
    const btn = ev.target.closest("[data-confirm]");
    if (!btn) return;
    if (btn.dataset.confirmed) { delete btn.dataset.confirmed; return; }
    ev.preventDefault();
    askConfirm(btn.dataset.confirm).then((ok) => {
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
    const input = document.querySelector("#upload-form input[type=file]");
    if (input) input.click(); else location.href = "/#nova";
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
      { id: "pg-hist", title: "Histórico", section: "Páginas", icon: icon("paw"), keywords: "publicados envios", handler: go("/historico") },
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
    const items = document.querySelectorAll("main > *, .grid > .thumb, .site-list > .card");
    items.forEach((el, i) => { el.classList.add("reveal"); el.style.setProperty("--i", Math.min(i, 14)); });

    // polaroids inclinam seguindo o mouse
    document.querySelectorAll(".thumb").forEach((card) => {
      card.addEventListener("pointermove", (ev) => {
        const r = card.getBoundingClientRect();
        const x = (ev.clientX - r.left) / r.width - 0.5, y = (ev.clientY - r.top) / r.height - 0.5;
        card.style.transform = `perspective(600px) rotateY(${x * 8}deg) rotateX(${-y * 8}deg) translateY(-3px)`;
      });
      card.addEventListener("pointerleave", () => { card.style.transform = ""; });
    });

    // coraçõezinhos ao clicar nos botões principais
    document.addEventListener("pointerdown", (ev) => {
      const btn = ev.target.closest("button.primary");
      if (!btn) return;
      for (let i = 0; i < 6; i++) {
        const h = document.createElement("span");
        h.className = "heart-pop"; h.textContent = i % 2 ? "♡" : "✿";
        h.style.left = ev.clientX + "px"; h.style.top = ev.clientY + "px";
        h.style.setProperty("--dx", (Math.random() * 80 - 40).toFixed(0) + "px");
        h.style.setProperty("--dy", (-30 - Math.random() * 50).toFixed(0) + "px");
        document.body.append(h);
        setTimeout(() => h.remove(), 800);
      }
    });
  }

  // "Subir nova arte" vindo de outra página: destaca a área de upload
  if (location.hash === "#nova") {
    const drop = document.getElementById("drop");
    if (drop) { drop.scrollIntoView({ block: "center" }); drop.classList.add("wiggle"); }
  }
})();
