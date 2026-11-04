(() => {
  "use strict";
  const Core = globalThis.AgatReviewForm;
  const bundle = Core.parseJson(document.getElementById("form-data").textContent);
  const $ = id => document.getElementById(id);
  const setText = (element, text) => { if (element.textContent !== text) element.textContent = text; };
  const storageKey = role => `agat-review-form:${bundle.manifest.formId}:${role}`;
  let storageAvailable = true, state, confirmAction = null;
  const now = () => new Date().toISOString();
  const notice = text => { setText($("notice"), text); $("notice").hidden = !text; };
  function restore(role) {
    try {
      const raw = localStorage.getItem(storageKey(role));
      if (raw) { const saved = Core.validateExport(Core.parseJson(raw), bundle); if (saved.participantRole !== role) throw new Error("Владелец сохранённого черновика изменён."); return saved; }
    } catch { storageAvailable = false; notice("Не удалось восстановить черновик в этом браузере. Можно загрузить ранее скачанный файл."); }
    return Core.createState(bundle, role);
  }
  state = restore("first");
  function persist() {
    if (state.status === "draft") state.updatedAt = now();
    try { localStorage.setItem(storageKey(state.participantRole), JSON.stringify(state)); storageAvailable = true; }
    catch { storageAvailable = false; }
    updateSummary();
  }
  function updateSummary() {
    const count = Core.count(state), total = state.answers.length, locked = state.status === "submitted";
    setText($("count"), `Отвечено ${count} из ${total}`);
    $("progress").max = total; $("progress").value = count;
    $("numbers").querySelectorAll("button").forEach((button, i) => {
      const done = Core.complete(state.answers[i]); button.classList.toggle("complete", done); button.classList.toggle("current", i === state.position);
      button.setAttribute("aria-label", `Вопрос ${i + 1}: ${done ? "есть ответ" : "без ответа"}`);
      if (i === state.position) button.setAttribute("aria-current", "step"); else button.removeAttribute("aria-current");
    });
    $("download-final").disabled = count !== total || !state.participantName.trim();
    $("reopen").hidden = !locked;
    setText($("storage"), storageAvailable ? (locked ? "Итоговые ответы сохранены в этом браузере." : "Черновик сохраняется в этом браузере.") : "Хранилище браузера недоступно. Скачайте черновик, чтобы не потерять ответы.");
    setText($("finish-hint"), locked ? "Прикрепите скачанный итоговый JSON к чату с Codex." : count !== total ? `Для итогового файла заполните ещё ${Core.questions(total - count)}${state.participantName.trim() ? "" : " и укажите своё имя"}.` : state.participantName.trim() ? "Все вопросы заполнены. Можно скачать итоговый файл." : "Все вопросы заполнены. Укажите имя или псевдоним для итогового файла.");
    setText($("characters"), `${$("rationale").value.length} / 4000`);
  }
  function node(tag, text, className) { const element = document.createElement(tag); if (text !== undefined) element.textContent = text; if (className) element.className = className; return element; }
  function renderSource(index) {
    const translated = bundle.localization.cases[index];
    setText($("source-title"), translated.title); $("source-body").replaceChildren();
    $("source-body").classList.remove("expanded"); $("expand-source").setAttribute("aria-expanded", "false");
    setText($("expand-source"), "Развернуть весь текст");
    let code = [];
    const flush = () => { if (!code.length) return; const details = node("details"), summary = node("summary", "Команды и вывод программы — исходный текст"); details.append(summary, node("pre", code.join("\n\n"))); $("source-body").append(details); code = []; };
    for (const paragraph of translated.paragraphs) {
      if (paragraph.kind === "code") code.push(paragraph.text);
      else { flush(); $("source-body").append(node("p", paragraph.text)); }
    }
    flush();
    updateSourceExpansion();
    const reference = bundle.blank.pool.cases[index].provenance.reference;
    $("attribution").replaceChildren();
    const urls = reference.match(/https:\/\/[^\s;)]+/g) || [];
    const source = urls.find(url => /\/(?:questions)\//.test(url));
    if (source) { const link = node("a", "Источник обращения"); link.href = source; link.target = "_blank"; link.rel = "noopener noreferrer"; $("attribution").append(link); }
    const author = reference.match(/; author (.+?) \((https:\/\/[^)]+)\)/);
    if (author) { const link = node("a", `Автор: ${author[1]}`); link.href = author[2]; link.target = "_blank"; link.rel = "noopener noreferrer"; $("attribution").append(link); }
    $("attribution").append(node("span", bundle.blank.pool.cases[index].provenance.kind === "synthetic" ? "Синтетическое задание для проверки интерфейса." : "CC BY-SA 4.0 · Русский перевод; технические цитаты сохранены."));
  }
  function updateSourceExpansion() {
    $("expand-source").hidden = !$("source-body").classList.contains("expanded") && $("source-body").scrollHeight <= $("source-body").clientHeight;
  }
  $("source-body").addEventListener("toggle", updateSourceExpansion, true);
  $("expand-source").addEventListener("click", () => {
    const expanded = $("source-body").classList.toggle("expanded");
    $("expand-source").setAttribute("aria-expanded", String(expanded));
    setText($("expand-source"), expanded ? "Свернуть текст" : "Развернуть весь текст"); updateSourceExpansion();
  });
  window.addEventListener("resize", updateSourceExpansion);
  function render(focus = false) {
    const answer = state.answers[state.position], locked = state.status === "submitted";
    $("role").value = state.participantRole; $("name").value = state.participantName; $("name").disabled = locked;
    setText($("position"), `Вопрос ${state.position + 1} из ${state.answers.length}`);
    renderSource(state.position); $("options").replaceChildren();
    bundle.manifest.options.forEach(option => {
      const label = node("label", undefined, "option"), radio = node("input"); radio.type = "radio"; radio.name = "answer"; radio.value = option.id; radio.checked = answer.optionId === option.id; radio.disabled = locked;
      const text = node("span"); text.append(node("strong", option.label), node("small", option.description)); label.append(radio, text); $("options").append(label);
      radio.addEventListener("change", () => { answer.optionId = option.id; answer.optionLabelRu = option.label; $("question-error").hidden = true; $("clear").disabled = false; persist(); });
    });
    $("rationale").value = answer.rationale; $("rationale").disabled = locked;
    $("back").disabled = state.position === 0; $("skip").disabled = locked; $("next").disabled = locked; $("clear").disabled = locked || (answer.optionId === null && !answer.rationale);
    $("question-error").hidden = true; updateSummary();
    if (focus) { $("question-title").focus({ preventScroll: true }); $("question-title").scrollIntoView({ block: "start", behavior: "instant" }); }
  }
  function go(position) { state.position = position; persist(); render(true); }
  function confirm(title, body, button, action) { setText($("confirm-title"), title); setText($("confirm-body"), body); setText($("accept-confirm"), button); confirmAction = action; $("confirm").showModal(); }
  $("cancel-confirm").addEventListener("click", () => { confirmAction = null; $("confirm").close(); });
  $("confirm").addEventListener("cancel", () => { confirmAction = null; });
  $("accept-confirm").addEventListener("click", () => { const action = confirmAction; confirmAction = null; $("confirm").close(); if (action) action(); });
  $("name").addEventListener("input", () => { state.participantName = $("name").value; persist(); });
  $("rationale").addEventListener("input", () => { state.answers[state.position].rationale = $("rationale").value; $("question-error").hidden = true; $("clear").disabled = false; persist(); });
  $("role").addEventListener("change", () => {
    const role = $("role").value; $("role").value = state.participantRole;
    confirm("Сменить участника?", "У каждого участника свой отдельный черновик. Ответы текущего участника сохранятся; в другой опрос они не переносятся.", "Сменить участника", () => { persist(); state = restore(role); render(); notice("Открыт отдельный опрос. Заполняйте его самостоятельно, без ответов другого участника."); });
  });
  $("back").addEventListener("click", () => go(Math.max(0, state.position - 1)));
  $("skip").addEventListener("click", () => { const next = state.answers.findIndex((a, i) => i > state.position && !Core.complete(a)); go(next < 0 ? Math.min(state.position + 1, state.answers.length - 1) : next); });
  $("next-empty").addEventListener("click", () => { const index = state.answers.findIndex(a => !Core.complete(a)); if (index < 0) notice("Все вопросы заполнены. Можно проверить ответы и скачать итоговый файл."); else go(index); });
  $("next").addEventListener("click", () => {
    if (!Core.complete(state.answers[state.position])) { setText($("question-error"), "Выберите вариант и напишите краткое обоснование на русском."); $("question-error").hidden = false; if (state.answers[state.position].optionId === null) $("options").querySelector("input").focus(); else $("rationale").focus(); return; }
    persist(); const next = state.answers.findIndex((a, i) => i > state.position && !Core.complete(a));
    if (next >= 0) go(next); else if (state.position < state.answers.length - 1) go(state.position + 1); else notice(Core.count(state) === state.answers.length ? "Все вопросы заполнены. Проверьте ответы и скачайте итоговый файл." : "Вы дошли до конца. Кнопка «К первому без ответа» вернёт к пропущенным вопросам.");
  });
  $("clear").addEventListener("click", () => confirm("Очистить этот ответ?", "Выбор и обоснование текущего вопроса будут удалены. Остальные ответы сохранятся.", "Очистить", () => { Object.assign(state.answers[state.position], { optionId: null, optionLabelRu: null, rationale: "" }); persist(); render(); }));
  function download(value, final) {
    const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2) + "\n"], { type: "application/json;charset=utf-8" }));
    const anchor = node("a"); anchor.href = url; anchor.download = `agat-review-${value.participantRole}-${final ? "final" : "draft"}-${now().replace(/[:.]/g, "-")}.json`;
    document.body.append(anchor); anchor.click(); anchor.remove(); setTimeout(() => URL.revokeObjectURL(url), 30000);
  }
  $("download-draft").addEventListener("click", () => {
    try { const draft = Core.makeExport(state, bundle); download(draft, state.status === "submitted"); notice(state.status === "submitted" ? "Сохранён итоговый файл ответов." : "Черновик скачан. Его можно загрузить в эту форму и продолжить позже."); } catch (error) { notice(error.message); }
  });
  $("download-final").addEventListener("click", () => {
    try {
      Core.makeExport(state, bundle, { submit: true });
      if (state.status === "submitted") { download(state, true); return; }
      confirm("Завершить опрос и скачать ответы?", `Вы заполнили ${Core.questions(state.answers.length)}. Подтвердите, что это ваши собственные решения. После скачивания приложите итоговый JSON к чату с Codex.`, "Подтвердить и скачать", () => {
        state = Core.makeExport(state, bundle, { submit: true }); persist(); render(); download(state, true); notice("Итоговый файл скачан. Прикрепите его к чату с Codex. При необходимости можно вернуться к правке.");
      });
    } catch (error) { notice(error.message); }
  });
  $("reopen").addEventListener("click", () => confirm("Вернуться к правке?", "Скачанный итоговый файл сохранится. После правки потребуется заново скачать итоговые ответы.", "Продолжить правку", () => { state.status = "draft"; state.submittedAt = null; state.submissionConfirmed = false; persist(); render(); }));
  $("load").addEventListener("click", () => $("file").click());
  $("file").addEventListener("change", async () => {
    const file = $("file").files[0]; $("file").value = ""; if (!file) return;
    try {
      if (file.size > 32 * 1024 * 1024) throw new Error("Файл слишком большой. Выберите JSON ответов или прежний review.json.");
      const candidate = Core.load(Core.parseJson(await file.text()), bundle, state.participantRole);
      const apply = () => { state = candidate; persist(); render(); notice("Файл загружен. Ваши ответы и текущий вопрос восстановлены."); };
      if (state.answers.some(a => a.optionId !== null || a.rationale) || state.participantName) confirm("Загрузить ответы из файла?", "Текущий черновик этого участника будет заменён. Если он нужен, сначала вернитесь и скачайте его.", "Загрузить", apply);
      else apply();
    } catch (error) { notice("Файл не загружен. " + error.message); }
  });
  bundle.manifest.cases.forEach((_case, index) => { const button = node("button", String(index + 1), "number"); button.type = "button"; button.addEventListener("click", () => go(index)); $("numbers").append(button); });
  setText($("intro"), `${Core.questions(state.answers.length)}. Выберите категорию и кратко объясните решение.`);
  setText($("instruction"), bundle.localization.instruction);
  if (matchMedia("(max-width: 700px)").matches) $("numbers").closest("details").open = false;
  render();
})();
