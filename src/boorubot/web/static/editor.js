/* boorubot · editor da arte (v2)
   Melhora o formulário do post sem depender dele: sem JavaScript, o <textarea name="tags">
   e os botões do form continuam funcionando como antes. */
(function () {
  "use strict";

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const pct = (v) => Math.round(v * 100) + "%";
  const SURE = 0.7; // a partir daqui a IA "tem certeza"
  const MOBILE_TAGS = 6; // no celular o campo mostra só as primeiras
  // mesmo critério do normalize_tags do backend: "long hair" -> long_hair
  const clean = (t) => t.trim().toLowerCase().split(/\s+/).filter(Boolean).join("_");
  const X_ICON = '<svg width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" aria-hidden="true"><path d="M5 5l10 10M15 5L5 15"/></svg>';
  // frases da barra (mesmas de MISSING_PHRASES no backend)
  const MISSING = { "sem tags": "faltam as tags", "escolha o rating": "falta escolher o rating", "nenhum site marcado": "falta marcar um site" };

  // ------------------------------------------------------------- tags
  // Cada tag vira uma etiqueta do design system (Tag): ponto verde (IA com certeza), amarela com %
  // (vale conferir), lilás (tag padrão das configurações), sem ponto (digitada por ela). O valor
  // enviado ao backend continua sendo o texto separado por espaço no textarea.
  function TagField(root) {
    const box = root.querySelector("textarea"); // tags do post ou tags padrão (Ajustes)
    const data = JSON.parse(root.dataset.tagField || "{}");
    let scores = data.scores || {};
    let weak = data.weak || [];
    const defaults = new Set(data.defaults || []);
    let unknown = new Set(); // etapa 12: tags que não existem no Danbooru

    root.classList.add("enhanced");
    // caixa com as etiquetas e o campo de digitar no fim (como no Figma); clicar no vazio foca o campo
    const tagBox = Object.assign(document.createElement("div"), { className: "ed-tagbox" });
    const list = Object.assign(document.createElement("ul"), { className: "bb-tags ed-tag-list", role: "list" });
    const input = Object.assign(document.createElement("input"), {
      type: "text", className: "ed-tag-input", placeholder: "digite uma tag e Enter…", autocomplete: "off", spellcheck: false,
    });
    input.setAttribute("aria-label", "Adicionar tag");
    input.id = box.id + "-new";
    // celular: o campo mostra 6 tags; "+ N" abre o campo inteiro numa folha (#tags-sheet)
    const more = Object.assign(document.createElement("button"), { type: "button", className: "bb-tag ed-tag-more", hidden: true });
    tagBox.append(list, more, input);
    more.addEventListener("click", () => {
      const sheet = document.getElementById("tags-sheet");
      if (!sheet || !window.boorubotDialog) return;
      const spot = document.createComment("tag-field");
      root.before(spot);
      sheet.querySelector("[data-tags-sheet-body]").append(root);
      window.boorubotDialog(sheet).then(() => spot.replaceWith(root));
      input.focus();
    });
    tagBox.addEventListener("click", (ev) => { if (ev.target === tagBox || ev.target === list) input.focus(); });
    const weakBox = Object.assign(document.createElement("div"), { className: "ed-weak" });
    const autocomplete = Object.assign(document.createElement("ul"), {
      className: "tag-suggestions bb-card bb-card--float", role: "listbox", hidden: true,
    });
    box.after(tagBox, autocomplete, weakBox);
    const countEl = document.querySelector("[data-tag-count]");
    const disabled = box.disabled || box.closest("fieldset[disabled]");
    if (disabled) input.hidden = true;

    const tags = () => box.value.split(/\s+/).filter(Boolean);
    let tagCheckTimer = null;
    let tagCheckSerial = 0;
    function setTags(next) {
      box.value = [...new Set(next)].join(" ");
      box.dispatchEvent(new Event("input", { bubbles: true }));
      box.dispatchEvent(new Event("change", { bubbles: true }));
      render();
    }

    async function checkUnknown() {
      clearTimeout(tagCheckTimer);
      const requested = tags();
      const serial = ++tagCheckSerial;
      if (!requested.length) { unknown = new Set(); render(); return true; }
      try {
        const response = await fetch("/danbooru/tags/check", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ tags: requested }), signal: AbortSignal.timeout(2500),
        });
        const result = await response.json();
        if (serial === tagCheckSerial && result.available && JSON.stringify(tags()) === JSON.stringify(requested)) {
          unknown = new Set(result.unknown || []);
          render();
        }
        return Boolean(result.available);
      } catch (e) { return false; }
    }
    function scheduleTagCheck() {
      clearTimeout(tagCheckTimer);
      tagCheckTimer = setTimeout(checkUnknown, 450);
    }
    box.addEventListener("input", scheduleTagCheck);
    box.addEventListener("change", scheduleTagCheck);

    function closeAutocomplete() {
      autocomplete.hidden = true;
      autocomplete.replaceChildren();
    }
    function chooseSuggestion(tag) {
      const raw = input.value;
      const split = Math.max(raw.lastIndexOf(" "), raw.lastIndexOf(","));
      input.value = (split >= 0 ? raw.slice(0, split + 1) : "") + tag;
      addTyped();
      closeAutocomplete();
      input.focus();
    }
    let autocompleteTimer = null;
    let autocompleteRequest = null;
    input.addEventListener("input", () => {
      clearTimeout(autocompleteTimer);
      if (autocompleteRequest) autocompleteRequest.abort();
      const token = input.value.trim().split(/[\s,]+/).pop().toLowerCase();
      if (token.length < 2 || disabled) { closeAutocomplete(); return; }
      autocompleteTimer = setTimeout(async () => {
        const query = token;
        autocompleteRequest = new AbortController();
        try {
          const response = await fetch("/danbooru/autocomplete.json?q=" + encodeURIComponent(query),
                                       { signal: autocompleteRequest.signal });
          const body = await response.json();
          if (!body.available || input.value.trim().split(/[\s,]+/).pop().toLowerCase() !== query) return;
          const matches = (body.suggestions || []).map((item) => typeof item === "string" ? item : item.value)
            .filter(Boolean).slice(0, 10);
          autocomplete.innerHTML = matches.map((tag) => `<li role="option"><button type="button" data-complete="${esc(tag)}">${esc(tag)}</button></li>`).join("");
          autocomplete.hidden = !matches.length;
        } catch (e) { closeAutocomplete(); }
      }, 300);
    });
    autocomplete.addEventListener("mousedown", (ev) => ev.preventDefault());
    autocomplete.addEventListener("click", (ev) => {
      const option = ev.target.closest("[data-complete]");
      if (option) chooseSuggestion(option.dataset.complete);
    });
    input.addEventListener("keydown", (ev) => {
      if (ev.key === "ArrowDown" && !autocomplete.hidden) {
        ev.preventDefault();
        autocomplete.querySelector("button")?.focus();
      }
    });

    // [classe do componente Tag, texto de apoio para leitor de tela]
    function kind(tag) {
      if (unknown.has(tag)) return [" bb-tag--new", "tag nova: confira a grafia"];
      if (defaults.has(tag) && !(tag in scores)) return [" bb-tag--mine", "sua tag padrão"];
      if (tag in scores) return scores[tag] >= SURE ? ["", "IA tem certeza"] : [" bb-tag--check", "vale conferir"];
      return [" bb-tag--own", ""];
    }

    function render() {
      const current = tags();
      list.innerHTML = current.map((t) => {
        const [cls, note] = kind(t);
        const extra = cls === " bb-tag--check" ? ` <span class="bb-tag__score">${pct(scores[t])}</span>` : "";
        const remove = disabled ? "" : `<button type="button" class="bb-tag__x" data-remove="${esc(t)}" aria-label="Remover ${esc(t)}">${X_ICON}</button>`;
        const sr = note ? `<span class="sr-only">, ${esc(note)}</span>` : "";
        return `<li class="bb-tag${cls}"${note ? ` title="${esc(note)}"` : ""}><span class="bb-tag__dot" aria-hidden="true"></span>${esc(t)}${sr}${extra}${remove}</li>`;
      }).join("");
      if (countEl) countEl.textContent = current.length + (current.length === 1 ? " escolhida" : " escolhidas");
      more.hidden = current.length <= MOBILE_TAGS;
      more.textContent = "+ " + (current.length - MOBILE_TAGS);
      more.setAttribute("aria-label", `ver todas as ${current.length} tags`);
      const visibleWeak = weak.filter(([t]) => !current.includes(t));
      weakBox.hidden = disabled || !visibleWeak.length;
      weakBox.innerHTML = visibleWeak.length ? `<span class="ed-weak__label">A IA também viu:</span>` +
        visibleWeak.map(([t, v]) => `<button type="button" class="bb-tag bb-tag--suggest" data-add="${esc(t)}">+ ${esc(t)} <span class="bb-tag__score">${pct(v)}</span></button>`).join("") +
        `<button type="button" class="bb-btn bb-btn--link" data-add-all>adicionar todas</button>` : "";
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
    input.addEventListener("blur", () => setTimeout(() => {
      if (!autocomplete.contains(document.activeElement)) { addTyped(); closeAutocomplete(); }
    }, 120));
    // colar "1girl, long hair" vira várias tags
    input.addEventListener("paste", () => setTimeout(() => { if (/[\s,]/.test(input.value.trim())) addTyped(); }));

    render();
    scheduleTagCheck();
    return {
      root, input, tags, setTags, render, checkUnknown,
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
  if (form && tagField) {
    let checkingForApprove = false;
    form.addEventListener("submit", async (ev) => {
      if (checkingForApprove || !ev.submitter?.matches("[data-approve]")) return;
      ev.preventDefault();
      await tagField.checkUnknown();
      checkingForApprove = true;
      form.requestSubmit(ev.submitter);
      checkingForApprove = false;
    });
  }
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
    if (why) { why.hidden = !errors.length; why.textContent = errors.map((e) => MISSING[e] || e).join(", "); }
    const when = document.querySelector("#approve-when b");
    if (when && when.previousSibling) when.previousSibling.textContent = errors.length ? "depois sai " : "se aprovar agora, sai ";
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

  // ------------------------------------------- a IA olhando a arte
  const suggestBtn = document.getElementById("suggest");
  const suggestInfo = document.getElementById("suggest-info");
  let suggestTimer = null;
  // fala da Mochi embaixo da arte (MochiNote): título, texto e expressão
  function mochiSays(title, text, face) {
    const note = document.getElementById("mochi-note");
    if (!note) return;
    note.hidden = !text;
    note.querySelector("img").src = `/static/pets/mochi-${face}.svg`;
    const t = note.querySelector(".bb-mochi__title");
    if (t) t.textContent = title;
    note.querySelector(".bb-mochi__text").textContent = text || "";
  }
  function showSuggest(st) {
    const busy = st.state === "pendente" || st.state === "analisando";
    if (st.state === "pendente") suggestInfo.textContent = st.waiting ? `a Mochi olha esta arte em seguida (${st.waiting} na frente)` : "a Mochi vai olhar a arte agora";
    else if (st.state === "analisando") suggestInfo.textContent = st.model_ready ? "a Mochi está olhando a arte…" : "na primeira vez a Mochi baixa o modelo de tags (380 MB): pode levar alguns minutos";
    else if (st.state === "falhou") { suggestInfo.textContent = "a Mochi não conseguiu olhar"; mochiSays("Não consegui olhar", st.error, "ops"); }
    else if (st.state === "pronta") {
      const added = tagField ? tagField.update(st) : 0;
      suggestInfo.textContent = added ? `+${added} tags da Mochi` : "";
      const oc = document.getElementById("original_character");
      if (oc && oc.checked !== st.original_character) { oc.checked = st.original_character; oc.dispatchEvent(new Event("change", { bubbles: true })); }
      mochiSays("A Mochi já olhou", st.mochi, "feliz");
      document.querySelectorAll(".bb-rating__opt").forEach((opt) => {
        const on = opt.querySelector("input").value === st.rating_suggestion;
        opt.classList.toggle("bb-rating__opt--suggested", on);
        const badge = opt.querySelector(".bb-rating__ai");
        badge.hidden = !on;
        if (on) badge.textContent = "sugestão da IA" + (st.rating_confidence ? " · " + pct(st.rating_confidence) : "");
      });
    }
    if (suggestBtn) suggestBtn.disabled = busy;
    if (busy && !suggestTimer) suggestTimer = setInterval(pollSuggest, 2000);
    if (!busy && suggestTimer) { clearInterval(suggestTimer); suggestTimer = null; }
  }
  async function pollSuggest() {
    try { showSuggest(await (await fetch(`/posts/${postId}/suggest/status`)).json()); } catch (e) {}
  }
  if (suggestBtn) suggestBtn.addEventListener("click", async () => {
    try { showSuggest(await (await fetch(`/posts/${postId}/suggest`, { method: "POST" })).json()); }
    catch (e) { suggestInfo.textContent = "Não deu para pedir: " + e.message; }
  });
  if (form && ["pendente", "analisando"].includes(form.dataset.suggestState)) pollSuggest();

  // ------------------------------------------------ publicar em
  if (form) {
    const oc = document.getElementById("original_character");
    const ocOnly = document.querySelector("[data-oc-only]");
    if (oc && ocOnly) oc.addEventListener("change", () => { ocOnly.hidden = !oc.checked; });
    const summary = document.querySelector("[data-sites-summary]");
    if (summary) form.addEventListener("change", () => {
      const total = form.querySelectorAll("input[name=sites][type=checkbox]").length;
      summary.textContent = `${value("sites").length} de ${total} site${total === 1 ? "" : "s"}`;
    });
    const count = document.querySelector("[data-sites-count]");
    if (count) form.addEventListener("change", () => {
      const n = value("sites").length;
      count.textContent = n + (n === 1 ? " site" : " sites");
    });
  }
  document.querySelectorAll("[data-open-envios]").forEach((a) => a.addEventListener("click", () => {
    const box = document.getElementById("envios");
    if (box) box.open = true;
    a.closest("details").open = false;
  }));
  // menu "Mais ações": fecha com Esc e ao clicar fora
  document.addEventListener("click", (ev) => {
    document.querySelectorAll(".ed-menu[open]").forEach((m) => { if (!m.contains(ev.target)) m.open = false; });
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key !== "Escape") return;
    document.querySelectorAll(".ed-menu[open]").forEach((m) => { m.open = false; m.querySelector("summary").focus(); });
  });

  // Ctrl+Enter aprova (só com o botão habilitado; não é atalho de digitação, então vale em qualquer campo)
  document.addEventListener("keydown", (ev) => {
    if (ev.key !== "Enter" || !(ev.ctrlKey || ev.metaKey) || ev.altKey || ev.shiftKey) return;
    const approve = document.querySelector("[data-approve]");
    if (!approve || approve.disabled || !form) return;
    ev.preventDefault();
    form.requestSubmit(approve);
  });

  window.boorubotEditor = { tagField, esc, pct, refreshApproval, approvalErrors, save };
})();
