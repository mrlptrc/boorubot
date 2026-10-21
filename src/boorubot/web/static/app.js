/* boorubot · interações do painel
   Componentes do design system (templates/_ui.html): toasts, confirmação em <dialog> nativo, menus.
   A busca Ctrl+K fica em search.js. No celular, as folhas (dialog.bb-sheet) fecham com Esc, no véu ou arrastando para baixo. */
(function () {
  "use strict";

  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

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
      const onCancel = (ev) => { ev.preventDefault(); dialog.close(""); };
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

  // data-confirm-dialog="id" usa um diálogo pronto da página (ex.: "Publicar agora" com a lista de sites);
  // senão, o genérico ganha título, texto, miniatura (data-confirm-thumb) e o rótulo da ação.
  function askConfirm(btn) {
    const own = btn.dataset.confirmDialog && document.getElementById(btn.dataset.confirmDialog);
    const dialog = own || document.getElementById("confirm-dialog");
    const text = btn.dataset.confirm;
    if (!dialog || !dialog.showModal) return Promise.resolve(window.confirm(text || "Tem certeza?"));
    if (own) return openDialog(own);
    dialog.querySelector("[data-dialog-title]").textContent = btn.dataset.confirmTitle || "Tem certeza?";
    dialog.querySelector("[data-dialog-text]").textContent = text;
    const thumb = dialog.querySelector("[data-dialog-thumb]");
    if (thumb) { thumb.hidden = !btn.dataset.confirmThumb; if (btn.dataset.confirmThumb) thumb.src = btn.dataset.confirmThumb; }
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
    const btn = ev.target.closest("[data-confirm], [data-confirm-dialog]");
    if (!btn) return;
    if (btn.dataset.confirmed) { delete btn.dataset.confirmed; return; }
    ev.preventDefault();
    askConfirm(btn).then((ok) => {
      if (!ok) return;
      btn.dataset.confirmed = "1";
      if (btn.form && btn.form.requestSubmit) btn.form.requestSubmit(btn); else btn.click();
    });
  }, true);

  // ------------------------------------------------------ copiar campo
  // botão com data-copy="#id": copia o valor do campo (envio à mão)
  document.addEventListener("click", async (ev) => {
    const btn = ev.target.closest("[data-copy]");
    if (!btn) return;
    const field = document.querySelector(btn.dataset.copy);
    if (!field) return;
    try {
      await navigator.clipboard.writeText(field.value);
    } catch (e) {  // sem permissão de área de transferência: seleciona para copiar com Ctrl+C
      field.select();
      document.execCommand && document.execCommand("copy");
    }
    toast("Copiado.", { kind: "sucesso", duration: 2500 });
  });

  // --------------------------------------------------- folhas no celular
  // clique no véu fecha; arrastar a folha para baixo (pela alça ou pelo topo) também
  document.querySelectorAll("dialog.bb-sheet").forEach((sheet) => {
    sheet.addEventListener("click", (ev) => { if (ev.target === sheet) sheet.close(""); });
    let startY = null;
    sheet.addEventListener("pointerdown", (ev) => {
      if (!ev.target.closest(".bb-sheet__grip, .bb-sheet__head") || ev.target.closest("a, button")) return;
      startY = ev.clientY; sheet.setPointerCapture(ev.pointerId);
    });
    sheet.addEventListener("pointermove", (ev) => {
      if (startY === null) return;
      sheet.style.transform = `translateY(${Math.max(0, ev.clientY - startY)}px)`;
    });
    const end = (ev) => {
      if (startY === null) return;
      const moved = ev.clientY - startY;
      startY = null; sheet.style.transform = "";
      if (moved > 80) sheet.close("");
    };
    sheet.addEventListener("pointerup", end);
    sheet.addEventListener("pointercancel", end);
  });

  // ------------------------------------------------------- animações
  if (!reducedMotion) {
    // entrada em cascata do conteúdo da página
    // o editor fica de fora: transform no pai prenderia a barra de ações fixa dentro dele
    const items = document.querySelectorAll("main > *:not(.editor), .grid > .thumb, .site-list > .card");
    items.forEach((el, i) => { el.classList.add("reveal"); el.style.setProperty("--i", Math.min(i, 14)); });

  }

})();
