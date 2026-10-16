/* boorubot · editor da arte (v2)
   Melhora o formulário do post sem depender dele: sem JavaScript, o <textarea name="tags">
   e os botões do form continuam funcionando como antes. */
(function () {
  "use strict";

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const pct = (v) => Math.round(v * 100) + "%";
  const SURE = 0.7; // a partir daqui a IA "tem certeza"
  // mesmo critério do normalize_tags do backend: "long hair" -> long_hair
  const clean = (t) => t.trim().toLowerCase().split(/\s+/).filter(Boolean).join("_");
  const X_ICON = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>';

  // ------------------------------------------------------------- tags
  // Cada tag vira uma etiqueta: verde (IA com certeza), amarela com % (vale conferir),
  // lilás (tag padrão das configurações), neutra (digitada por ela). O valor enviado ao
  // backend continua sendo o texto separado por espaço no textarea.
  function TagField(root) {
    const box = root.querySelector("textarea[name=tags]");
    const data = JSON.parse(root.dataset.tagField || "{}");
    let scores = data.scores || {};
    let weak = data.weak || [];
    const defaults = new Set(data.defaults || []);
    let unknown = new Set(); // etapa 12: tags que não existem no Danbooru

    root.classList.add("enhanced");
    const list = Object.assign(document.createElement("ul"), { className: "tag-chips", role: "list" });
    const input = Object.assign(document.createElement("input"), {
      type: "text", className: "tag-input", placeholder: "nova tag + Enter", autocomplete: "off", spellcheck: false,
    });
    input.setAttribute("aria-label", "Adicionar tag");
    input.id = box.id + "-new";
    const weakBox = Object.assign(document.createElement("div"), { className: "tag-weak" });
    box.after(list, input, weakBox);
    const disabled = box.disabled || box.closest("fieldset[disabled]");
    if (disabled) input.hidden = true;

    const tags = () => box.value.split(/\s+/).filter(Boolean);
    function setTags(next) {
      box.value = [...new Set(next)].join(" ");
      box.dispatchEvent(new Event("input", { bubbles: true }));
      box.dispatchEvent(new Event("change", { bubbles: true }));
      render();
    }

    function kind(tag) {
      if (unknown.has(tag)) return ["new", "tag nova: confira a grafia"];
      if (defaults.has(tag) && !(tag in scores)) return ["default", "tag padrão"];
      if (tag in scores) return scores[tag] >= SURE ? ["sure", "IA tem certeza"] : ["check", "vale conferir: IA " + pct(scores[tag])];
      return ["own", ""];
    }

    function render() {
      const current = tags();
      list.innerHTML = current.map((t) => {
        const [k, title] = kind(t);
        const extra = k === "check" ? ` <span class="tag-pct">${pct(scores[t])}</span>` : "";
        const remove = disabled ? "" : `<button type="button" class="tag-x" data-remove="${esc(t)}" aria-label="remover ${esc(t)}">${X_ICON}</button>`;
        return `<li class="tag tag-${k}"${title ? ` title="${esc(title)}"` : ""}><span>${esc(t)}</span>${extra}${remove}</li>`;
      }).join("");
      const visibleWeak = weak.filter(([t]) => !current.includes(t));
      weakBox.hidden = disabled || !visibleWeak.length;
      weakBox.innerHTML = visibleWeak.length ? `<span class="tag-weak-label">A IA também viu:</span>` +
        visibleWeak.map(([t, v]) => `<button type="button" class="tag-add" data-add="${esc(t)}">+ ${esc(t)} <span class="tag-pct">${pct(v)}</span></button>`).join("") +
        `<button type="button" class="link-btn" data-add-all>adicionar todas</button>` : "";
    }

    list.addEventListener("click", (ev) => {
      const b = ev.target.closest("[data-remove]");
      if (!b) return;
      const idx = [...list.querySelectorAll("[data-remove]")].indexOf(b);
      setTags(tags().filter((t) => t !== b.dataset.remove));
      // foco vai para a próxima etiqueta (ou para o campo), não se perde no documento
      const next = list.querySelectorAll("[data-remove]")[idx] || list.querySelectorAll("[data-remove]")[idx - 1];
      (next || input).focus();
    });
    weakBox.addEventListener("click", (ev) => {
      const one = ev.target.closest("[data-add]");
      if (one) { scores[one.dataset.add] = Object.fromEntries(weak)[one.dataset.add]; setTags([...tags(), one.dataset.add]); input.focus(); }
      if (ev.target.closest("[data-add-all]")) { weak.forEach(([t, v]) => { scores[t] = v; }); setTags([...tags(), ...weak.map(([t]) => t)]); input.focus(); }
    });

    function addTyped() {
      const raw = input.value;
      const items = (raw.includes(",") ? raw.split(",") : raw.split(/\s+/)).map(clean).filter(Boolean);
      if (!items.length) return false;
      setTags([...tags(), ...items]);
      input.value = "";
      return true;
    }
    input.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" && !ev.ctrlKey && !ev.metaKey) {
        ev.preventDefault();
        addTyped();
      } else if (ev.key === "Backspace" && !input.value && tags().length) {
        const last = tags()[tags().length - 1];
        setTags(tags().slice(0, -1));
        input.value = last;
      }
    });
    input.addEventListener("blur", addTyped);
    // colar "1girl, long hair" vira várias tags
    input.addEventListener("paste", () => setTimeout(() => { if (/[\s,]/.test(input.value.trim())) addTyped(); }));

    render();
    return {
      root, input, tags, setTags, render,
      update(next) { // sugestão da IA chegou: junta sem apagar o que ela tem
        scores = { ...scores, ...(next.scores || {}) };
        weak = next.weak || weak;
        const current = tags();
        const added = (next.tags || []).filter((t) => !current.includes(t));
        if (added.length) setTags([...current, ...added]); else render();
        return added.length;
      },
      markUnknown(set) { unknown = new Set(set); render(); },
    };
  }

  const tagRoot = document.querySelector("[data-tag-field]");
  const tagField = tagRoot ? TagField(tagRoot) : null;

  // ------------------------------------------------- pode aprovar?
  // Mesma lista do backend (approval_errors): sem tags, sem rating, sem site. O botão fica
  // desligado com o motivo à vista; sem JavaScript o backend recusa com a mesma mensagem.
  const form = document.getElementById("post-form");
  function approvalErrors() {
    if (!form) return [];
    const errors = [];
    if (!form.querySelector("textarea[name=tags]").value.trim()) errors.push("sem tags");
    if (!form.querySelector("input[name=rating]:checked")) errors.push("escolha o rating");
    if (!form.querySelector("input[name=sites]:checked")) errors.push("nenhum site marcado");
    return errors;
  }
  function refreshApproval() {
    const errors = approvalErrors();
    document.querySelectorAll("[data-needs-ready]").forEach((b) => {
      b.disabled = errors.length > 0;
      if (errors.length) b.setAttribute("aria-describedby", "approve-why"); else b.removeAttribute("aria-describedby");
    });
    const why = document.getElementById("approve-why");
    if (why) { why.hidden = !errors.length; why.textContent = errors.length ? "Falta: " + errors.join(", ") : ""; }
    return errors;
  }
  if (form) {
    form.addEventListener("input", refreshApproval);
    form.addEventListener("change", refreshApproval);
    refreshApproval();
  }

  // ------------------------------------------------- salvar sozinho
  // Ao sair de um campo de texto, ou ~800 ms depois de mexer em tags/rating/sites/OC, manda só
  // o que mudou (PATCH /posts/{id}). Falhou? Tenta de novo, cada vez esperando um pouco mais.
  const statusEl = document.querySelector("[data-save-status]");
  const postId = form && form.dataset.postId;
  const editable = form && !form.querySelector("fieldset[disabled]");
  const value = (name) => {
    if (name === "sites") return [...form.querySelectorAll("input[name=sites]:checked")].map((i) => i.value);
    if (name === "rating") { const r = form.querySelector("input[name=rating]:checked"); return r ? r.value : ""; }
    if (name === "original_character") return form.querySelector("input[name=original_character]").checked;
    return form.querySelector(`[name=${name}]`).value;
  };
  const FIELDS = ["title", "tags", "rating", "source", "commentary_title", "commentary_description", "original_character", "sites"];
  const snapshot = () => Object.fromEntries(FIELDS.filter((f) => form.querySelector(`[name=${f}]`)).map((f) => [f, JSON.stringify(value(f))]));
  let saved = form ? snapshot() : {};
  let debounce = null, retry = null, retryWait = 2000, inflight = null;

  function say(text, kind) {
    if (!statusEl) return;
    statusEl.textContent = text;
    statusEl.dataset.kind = kind;
  }
  function changed() {
    const now = snapshot();
    return Object.fromEntries(Object.keys(now).filter((k) => now[k] !== saved[k]).map((k) => [k, JSON.parse(now[k])]));
  }
  async function save({ keepalive = false } = {}) {
    clearTimeout(debounce); clearTimeout(retry);
    if (inflight) { await inflight.catch(() => {}); }
    const diff = changed();
    if (!Object.keys(diff).length) return;
    say("salvando…", "busy");
    inflight = fetch(`/posts/${postId}`, {
      method: "PATCH", keepalive, headers: { "Content-Type": "application/json" }, body: JSON.stringify(diff),
    }).then(async (r) => {
      const body = await r.json().catch(() => ({}));
      if (r.status === 409) { say(body.error || "não dá mais para editar esta arte", "error"); return; }
      if (!r.ok) throw new Error(body.error || r.status);
      Object.keys(diff).forEach((k) => { saved[k] = JSON.stringify(diff[k]); });
      retryWait = 2000;
      say("salvo às " + body.saved_at, "ok");
      if (Object.keys(changed()).length) schedule(); // ela mexeu de novo enquanto salvava
    }).catch(() => {
      say("não salvou, tentando de novo", "error");
      retry = setTimeout(save, retryWait);
      retryWait = Math.min(retryWait * 2, 30000);
    }).finally(() => { inflight = null; });
    return inflight;
  }
  function schedule() { clearTimeout(debounce); debounce = setTimeout(save, 800); }

  if (form && postId && editable) {
    form.addEventListener("focusout", (ev) => {
      if (ev.target.matches("input[type=text], input:not([type]), textarea")) save();
    });
    form.addEventListener("change", (ev) => {
      if (ev.target.matches("input[type=checkbox], input[type=radio], textarea[name=tags]")) schedule();
    });
    // clicar numa ação (Aprovar, Testar…) manda o form inteiro: o salvamento pendente não precisa correr
    form.addEventListener("submit", () => { clearTimeout(debounce); clearTimeout(retry); saved = snapshot(); });
    window.addEventListener("pagehide", () => { if (Object.keys(changed()).length) save({ keepalive: true }); });
  }

  window.boorubotEditor = { tagField, esc, pct, refreshApproval, approvalErrors, save };
})();
