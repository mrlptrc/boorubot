/* boorubot · Ajustes
   Cada mudança salva sozinha (PATCH /ajustes, só o campo que mudou), com "salvo às 14:05" no topo.
   Sem JavaScript, os forms da página mandam para as rotas /settings* com os botões Salvar. */
(function () {
  "use strict";

  const status = document.querySelector("[data-save-status]");
  const say = (text, kind) => { status.textContent = text; status.dataset.kind = kind; };
  const fields = document.querySelectorAll("[data-setting]");
  // sem o botão Salvar: os forms só existem para o plano B
  document.querySelectorAll("form").forEach((f) => {
    if (f.closest("main") && f.querySelector("[data-setting]")) f.addEventListener("submit", (ev) => {
      if (!ev.submitter || !ev.submitter.getAttribute("formaction")) ev.preventDefault();
    });
  });

  function value(name) {
    if (name === "default_sites") return [...document.querySelectorAll("[data-setting=default_sites]:checked")].map((i) => i.value);
    const el = document.querySelector(`[data-setting=${name}]`);
    if (el.type === "checkbox") return el.checked;
    if (name === "default_tags") return el.value.split(/\s+/).filter(Boolean);
    return el.value;
  }

  function showBackup(st) {
    const line = document.querySelector("[data-backup-last]");
    if (!line) return;
    line.textContent = !st.backup_on ? "backup desligado"
      : st.last_backup ? `último backup ${st.last_backup} · ${st.last_backup_ok === "1" ? "deu tudo certo" : "falhou: " + st.last_backup_msg}`
      : "ainda não rodou";
  }

  let timer = null;
  const pending = {};
  async function flush() {
    const body = { ...pending };
    Object.keys(pending).forEach((k) => delete pending[k]);
    if (!Object.keys(body).length) return;
    say("salvando…", "busy");
    try {
      const r = await fetch("/ajustes", { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const data = await r.json();
      if (!r.ok) {
        say("não salvou: " + (data.error || r.status), "error");
        if (data.state) {
          const bk = document.querySelector("[data-setting=backup_on]");
          if (bk) bk.checked = data.state.backup_on;
          const st = document.querySelector("[data-setting=startup]");
          if (st) st.checked = data.state.startup.installed;
        }
        return;
      }
      say("salvo às " + data.saved_at, "ok");
      showBackup(data.state);
      const mirror = document.querySelector("[data-mirror-test-mode]");
      if (mirror) mirror.value = data.state.test_mode ? "true" : "";
      // o modo teste muda a faixa e o card do robô: recarrega para tudo ficar igual
      if ("test_mode" in body) setTimeout(() => location.reload(), 400);
    } catch (e) {
      say("não salvou, sem conexão com o painel", "error");
    }
  }
  function queue(name, now) {
    pending[name] = value(name);
    if (name === "backup_on" || name === "backup_dir") { pending.backup_on = value("backup_on"); pending.backup_dir = value("backup_dir"); }
    clearTimeout(timer);
    timer = setTimeout(flush, now ? 0 : 800);
  }

  fields.forEach((el) => {
    const name = el.dataset.setting;
    if (el.matches("input[type=checkbox]")) el.addEventListener("change", () => queue(name, true));
    else if (el.matches("textarea")) el.addEventListener("change", () => queue(name));
    else { el.addEventListener("change", () => queue(name)); el.addEventListener("blur", () => queue(name, true)); }
  });
})();
