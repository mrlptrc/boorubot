/* boorubot · janela "Novas artes"
   Várias artes de uma vez: cada uma sobe (uma por vez), entra na fila da IA e a janela mostra
   o estado de cada uma. Fechar a janela não cancela nada: a análise continua no worker.
   Sem JavaScript, o "+ Nova arte" do Início continua sendo o form de sempre. */
(function () {
  "use strict";

  const dialog = document.getElementById("new-art");
  if (!dialog || typeof dialog.showModal !== "function") return;

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const EXTS = [".png", ".jpg", ".jpeg", ".gif", ".webp"];
  const list = dialog.querySelector("[data-na-list]");
  const input = dialog.querySelector("input[type=file]");
  const drop = dialog.querySelector("[data-na-drop]");
  const firstTime = dialog.querySelector("[data-na-first]");
  const summary = dialog.querySelector("[data-na-summary]");
  const reviewFirst = dialog.querySelector("[data-na-review]");
  const pick = dialog.querySelector("[data-na-pick]");
  const fallback = document.querySelector("[data-na-fallback]");

  // O formulário tradicional continua disponível quando JavaScript ou <dialog> não existe.
  if (fallback) fallback.hidden = true;

  let items = [];       // {key, file, url, state, progress, post, error, info, dup}
  let uploading = false;
  let pollTimer = null;
  let changedSomething = false;
  let seq = 0;

  // ------------------------------------------------------------ render
  const STATE_TEXT = {
    waiting: "esperando a vez",
    uploading: "enviando…",
    pendente: "na fila da Mochi",
    analisando: "a Mochi está olhando",
    pronta: "",
    falhou: "a Mochi não conseguiu olhar",
    error: "não subiu",
  };

  const MOCHI_THINKING = '<img class="na-mochi" src="/static/pets/mochi-pensando.svg" alt="" width="22" height="19">';

  function row(it) {
    const busy = it.state === "uploading" || it.state === "analisando" || it.state === "pendente";
    let text = STATE_TEXT[it.state] || "";
    let tone = "";
    if (it.state === "pronta") { text = "pronta · " + it.info; tone = " is-ok"; }
    if (it.state === "pendente" && it.waiting) text = `na fila da Mochi (${it.waiting} na frente)`;
    if (it.state === "error" || it.state === "falhou") { text += it.error ? ": " + it.error : ""; tone = " is-bad"; }
    const pct = it.state === "uploading" ? it.progress : null;
    const bar = busy ? `<span class="na-bar${pct === null ? " indeterminate" : ""}" role="progressbar" aria-label="${esc(text)}"
        ${pct !== null ? `aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}"` : ""}><span style="width:${pct !== null ? pct : 40}%"></span></span>` : "";
    let actions = "";
    if (it.state === "waiting") actions = `<button type="button" class="bb-btn bb-btn--link na-quiet" data-remove="${it.key}">tirar</button>`;
    if (it.post && it.state !== "error") actions = `<a class="bb-btn bb-btn--link" href="/posts/${it.post.id}">Revisar</a>`;
    if (it.dup) {
      text = it.dup.published_at ? `essa arte já saiu em ${it.dup.published_label}` : `essa arte já está ${it.dup.where}`;
      tone = " is-warn";
      actions = `<a class="bb-btn bb-btn--link" href="/posts/${it.dup.id}">abrir a que existe</a>` +
        `<button type="button" class="bb-btn bb-btn--secondary bb-btn--sm" data-force="${it.key}">Subir mesmo assim</button>`;
    }
    const thinking = it.state === "analisando" || it.state === "pendente" ? MOCHI_THINKING : "";
    return `<li class="na-item na-${it.state}">
      <img src="${it.url}" alt="">
      <div class="na-text"><span class="na-name">${esc(it.file.name)}</span><span class="na-state${tone}">${thinking}${esc(text)}</span>${bar}</div>
      <div class="na-actions">${actions}</div></li>`;
  }

  function render() {
    list.innerHTML = items.map(row).join("");
    list.hidden = !items.length;
    const ready = items.filter((i) => i.state === "pronta").length;
    summary.textContent = items.length ? `${ready} de ${items.length} pronta${items.length === 1 ? "" : "s"}` : "";
    const first = items.find((i) => i.state === "pronta") || items.find((i) => i.post);
    reviewFirst.hidden = !first;
    if (first) reviewFirst.href = `/posts/${first.post.id}`;
  }

  // ------------------------------------------------------------ envio
  function add(files) {
    for (const file of files) {
      const ok = EXTS.some((e) => file.name.toLowerCase().endsWith(e));
      items.push({
        key: String(++seq), file, url: URL.createObjectURL(file), progress: 0,
        state: ok ? "waiting" : "error", error: ok ? "" : "use PNG, JPG, GIF ou WEBP",
      });
    }
    render();
    pump();
  }

  function send(it, force) {
    return new Promise((resolve) => {
      const data = new FormData();
      data.append("image", it.file, it.file.name);
      if (force) data.append("repetida", "1");
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/posts");
      xhr.setRequestHeader("Accept", "application/json");
      xhr.upload.onprogress = (ev) => {
        if (ev.lengthComputable) { it.progress = Math.round((100 * ev.loaded) / ev.total); render(); }
      };
      xhr.onload = () => {
        let body = {};
        try { body = JSON.parse(xhr.responseText); } catch (e) {}
        if (body.duplicates && body.duplicates.length) {
          it.state = "duplicate"; it.dup = body.duplicates[0].existing;
        } else if (body.posts && body.posts.length) {
          it.post = body.posts[0]; it.state = "pendente"; changedSomething = true;
        } else {
          it.state = "error"; it.error = (body.errors && body.errors[0] && body.errors[0].error) || body.detail || "erro " + xhr.status;
        }
        resolve();
      };
      xhr.onerror = () => { it.state = "error"; it.error = "sem conexão com o painel"; resolve(); };
      it.state = "uploading"; it.progress = 0;
      render();
      xhr.send(data);
    });
  }

  async function pump() {
    if (uploading) return;
    uploading = true;
    let next;
    while ((next = items.find((i) => i.state === "waiting"))) {
      await send(next, next.force);
      render();
      poll();
    }
    uploading = false;
    if (changedSomething && !dialog.open && location.pathname === "/") location.reload();
  }

  // ------------------------------------------------------------ IA
  async function poll() {
    const watching = items.filter((i) => i.post && ["pendente", "analisando"].includes(i.state));
    if (!watching.length) { clearInterval(pollTimer); pollTimer = null; return; }
    if (!pollTimer) pollTimer = setInterval(poll, 2000);
    try {
      const st = await (await fetch("/suggest/status.json?ids=" + watching.map((i) => i.post.id).join(","))).json();
      showFirstTime(st.model_ready);
      for (const s of st.posts) {
        const it = items.find((i) => i.post && i.post.id === s.id);
        if (!it) continue;
        it.state = s.state || "pendente";
        it.waiting = s.waiting;
        it.error = s.error;
        if (s.state === "pronta") it.info = `${s.tags.length} tags` + (s.rating_label ? `, rating ${s.rating_label}` : "");
      }
      render();
    } catch (e) {}
  }

  function showFirstTime(ready) {
    firstTime.hidden = ready !== false;
  }

  // ------------------------------------------------------------ eventos
  list.addEventListener("click", (ev) => {
    const remove = ev.target.closest("[data-remove]");
    if (remove) {
      items = items.filter((i) => i.key !== remove.dataset.remove || i.state !== "waiting");
      render();
      pick.focus();
    }
    const force = ev.target.closest("[data-force]");
    if (force) {
      const it = items.find((i) => i.key === force.dataset.force);
      it.dup = null; it.state = "waiting"; it.force = true;
      render(); pump();
    }
  });
  input.addEventListener("change", () => { add(input.files); input.value = ""; });
  pick.addEventListener("click", () => input.click());
  ["dragenter", "dragover"].forEach((e) => drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((e) => drop.addEventListener(e, () => drop.classList.remove("over")));
  drop.addEventListener("drop", (ev) => { ev.preventDefault(); add(ev.dataTransfer.files); });
  dialog.querySelectorAll("[data-na-close]").forEach((b) => b.addEventListener("click", () => dialog.close()));
  dialog.addEventListener("close", () => {
    // nada é cancelado; no Início, recarrega para as artes novas aparecerem em "Para revisar"
    if (changedSomething && location.pathname === "/" && !uploading) location.reload();
  });

  async function open(files) {
    if (!dialog.open) dialog.showModal();
    try { showFirstTime((await (await fetch("/suggest/status.json")).json()).model_ready); } catch (e) {}
    if (files && files.length) add(files);
  }

  document.addEventListener("click", (ev) => {
    const trigger = ev.target.closest("[data-new-art]");
    if (!trigger) return;
    ev.preventDefault();
    open();
  });
  // arrastar artes para qualquer página abre a janela já com elas
  document.addEventListener("dragover", (ev) => { if (ev.dataTransfer && [...ev.dataTransfer.types].includes("Files")) ev.preventDefault(); });
  document.addEventListener("drop", (ev) => {
    if (dialog.open || !ev.dataTransfer || !ev.dataTransfer.files.length) return;
    ev.preventDefault();
    open(ev.dataTransfer.files);
  });

  window.boorubotNewArt = { open };
})();
