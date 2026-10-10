/* Portable form state. No network, predictions or default choices. */
(() => {
  "use strict";
  const SCHEMA = "agat.decision.review-form-export.v1";
  const ROLES = { first: "user-development-review", expert: "independent-expert-development-review" };
  const KEYS = ["schemaVersion", "formId", "language", "initialReviewFileSha256", "poolSha256", "splitSeed", "localizationFileSha256", "participantRole", "participantName", "reviewerId", "startedAt", "updatedAt", "submittedAt", "status", "submissionConfirmed", "position", "answers"];
  const ANSWER_KEYS = ["id", "inputSha256", "titleRu", "optionId", "optionLabelRu", "rationale"];
  const copy = value => JSON.parse(JSON.stringify(value));
  const require = (condition, message) => { if (!condition) throw new Error(message); };
  const fields = (value, keys) => require(value && typeof value === "object" && !Array.isArray(value) && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key)), "Неверный состав полей файла.");
  const validString = (value, limit, empty = false) => {
    if (typeof value !== "string" || value.length > limit || (!empty && !value.trim())) return false;
    try { encodeURIComponent(value); return true; } catch { return false; }
  };
  const russian = value => /[А-Яа-яЁё]/u.test(value);
  const complete = answer => answer.optionId !== null && Boolean(answer.rationale.trim()) && russian(answer.rationale);
  const canonical = value => JSON.stringify(value, (_key, item) => item && typeof item === "object" && !Array.isArray(item) ? Object.fromEntries(Object.keys(item).sort().map(key => [key, item[key]])) : item);
  const timestamp = value => typeof value === "string" && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$/.test(value) && Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0,19) === value.slice(0,19);

  function createState(bundle, role = "first", now = new Date().toISOString()) {
    require(typeof role === "string" && Object.hasOwn(ROLES, role), "Неизвестный участник.");
    const m = bundle.manifest;
    return { schemaVersion: SCHEMA, formId: m.formId, language: "ru", initialReviewFileSha256: m.initialReviewFileSha256,
      poolSha256: m.poolSha256, splitSeed: m.splitSeed, localizationFileSha256: m.localizationFileSha256,
      participantRole: role, participantName: "", reviewerId: ROLES[role], startedAt: now, updatedAt: now,
      submittedAt: null, status: "draft", submissionConfirmed: false, position: 0,
      answers: m.cases.map(c => ({ id: c.id, inputSha256: c.inputSha256, titleRu: c.title, optionId: null, optionLabelRu: null, rationale: "" })) };
  }

  function validateExport(value, bundle) {
    fields(value, KEYS);
    require(value.schemaVersion === SCHEMA && value.language === "ru", "Это не файл русскоязычного опроса.");
    const m = bundle.manifest;
    for (const key of ["formId", "initialReviewFileSha256", "poolSha256", "splitSeed", "localizationFileSha256"]) require(value[key] === m[key], "Файл относится к другому набору вопросов или переводу.");
    require(typeof value.participantRole === "string" && Object.hasOwn(ROLES, value.participantRole) && value.reviewerId === ROLES[value.participantRole], "Участник и владелец ответов не совпадают.");
    require(validString(value.participantName, 100, true), "Имя или псевдоним должно быть не длиннее 100 символов.");
    require(timestamp(value.startedAt) && timestamp(value.updatedAt) && Date.parse(value.startedAt) <= Date.parse(value.updatedAt), "Некорректные даты файла.");
    require(Number.isInteger(value.position) && value.position >= 0 && value.position < m.cases.length, "Некорректный номер вопроса.");
    require(value.status === "draft" || value.status === "submitted", "Неизвестное состояние опроса.");
    const submitted = value.status === "submitted";
    require(value.submissionConfirmed === submitted && (submitted ? value.submittedAt === value.updatedAt : value.submittedAt === null), "Некорректное подтверждение завершения.");
    require(Array.isArray(value.answers) && value.answers.length === m.cases.length, "Количество ответов не совпадает с опросом.");
    const options = new Map(m.options.map(o => [o.id, o.label]));
    value.answers.forEach((answer, index) => {
      fields(answer, ANSWER_KEYS);
      const c = m.cases[index];
      require(answer.id === c.id && answer.inputSha256 === c.inputSha256 && answer.titleRu === c.title, "Порядок или содержание вопросов в файле изменены.");
      require(answer.optionId === null || options.has(answer.optionId), "В файле есть неизвестный вариант ответа.");
      require(answer.optionLabelRu === (answer.optionId === null ? null : options.get(answer.optionId)), "Русский текст ответа не совпадает с выбранным вариантом.");
      require(validString(answer.rationale, 4000, true), "Обоснование должно быть не длиннее 4000 символов.");
      require(!submitted || complete(answer), "Итоговый файл содержит незаполненные ответы или обоснование не на русском.");
    });
    require(!submitted || Boolean(value.participantName.trim()), "Укажите своё имя или псевдоним.");
    return copy(value);
  }

  function makeExport(state, bundle, { submit = false, now = new Date().toISOString() } = {}) {
    const value = copy(state);
    value.participantName = value.participantName.trim();
    if (state.status !== "submitted") value.updatedAt = now;
    if (submit && state.status !== "submitted") {
      value.status = "submitted"; value.submissionConfirmed = true; value.submittedAt = now;
    }
    return validateExport(value, bundle);
  }

  function load(value, bundle, role, now = new Date().toISOString()) {
    if (value && value.schemaVersion === SCHEMA) return validateExport(value, bundle);
    fields(value, ["schemaVersion", "pool", "poolSha256", "splitSeed", "reviewerId", "reviewedAt", "labels"]);
    const blank = bundle.blank;
    require(value.schemaVersion === "agat.decision.review.v1" && canonical(value.pool) === canonical(blank.pool) && value.poolSha256 === blank.poolSha256 && value.splitSeed === blank.splitSeed, "Прежний черновик относится к другому набору вопросов.");
    require(value.reviewedAt === null, "Завершённый терминальный опрос нельзя выдать за новый черновик.");
    const ownerRole = Object.keys(ROLES).find(key => ROLES[key] === value.reviewerId);
    require(value.reviewerId === null || ownerRole, "Владелец прежнего черновика не совпадает с участниками этого опроса.");
    const state = createState(bundle, ownerRole || role, now);
    require(Array.isArray(value.labels) && value.labels.length === state.answers.length, "Количество прежних ответов не совпадает.");
    const options = new Map(bundle.manifest.options.map(o => [o.id, o.label]));
    value.labels.forEach((label, i) => {
      fields(label, ["id", "inputSha256", "expectedOptionId", "rationale"]);
      require(label.id === state.answers[i].id && label.inputSha256 === state.answers[i].inputSha256, "Привязка прежнего ответа к вопросу изменена.");
      if (label.expectedOptionId === null && label.rationale === null) return;
      require(value.reviewerId !== null && options.has(label.expectedOptionId) && validString(label.rationale, 4000), "Прежний ответ не содержит допустимый выбор и обоснование.");
      Object.assign(state.answers[i], { optionId: label.expectedOptionId, optionLabelRu: options.get(label.expectedOptionId), rationale: label.rationale });
    });
    state.position = Math.max(0, state.answers.findIndex(a => !complete(a)));
    return state;
  }

  function parseJson(raw) {
    // JSON.parse silently accepts duplicate object keys. Refuse them before an import can replace a draft.
    require(typeof raw === "string" && raw.length <= 32 * 1024 * 1024, "Файл слишком большой.");
    let parsed;
    try { parsed = JSON.parse(raw); } catch { throw new Error("Не удалось прочитать JSON. Выберите скачанный файл ответов."); }
    let p = 0;
    const whitespace = () => { while (/\s/.test(raw[p] || "") && p < raw.length) p++; };
    const readString = () => { const start = p++; while (p < raw.length) { const c = raw[p++]; if (c === "\\") p++; else if (c === '"') break; } return JSON.parse(raw.slice(start, p)); };
    function value(depth = 0) {
      require(depth < 80, "Слишком сложная структура файла."); whitespace();
      if (raw[p] === '"') { readString(); return; }
      if (raw[p] === "{") {
        p++; whitespace(); const keys = new Set();
        while (raw[p] !== "}") { whitespace(); const key = readString(); require(!keys.has(key), "В JSON есть повторяющиеся поля."); keys.add(key); whitespace(); p++; value(depth + 1); whitespace(); if (raw[p] === ",") p++; else break; }
        p++; return;
      }
      if (raw[p] === "[") { p++; whitespace(); while (raw[p] !== "]") { value(depth + 1); whitespace(); if (raw[p] === ",") p++; else break; } p++; return; }
      while (p < raw.length && !/[\s,}\]]/.test(raw[p])) p++;
    }
    value(); return parsed;
  }
  const questions = n => `${n} ${n % 100 >= 11 && n % 100 <= 14 ? "вопросов" : n % 10 === 1 ? "вопрос" : n % 10 >= 2 && n % 10 <= 4 ? "вопроса" : "вопросов"}`;
  globalThis.AgatReviewForm = Object.freeze({ SCHEMA, ROLES, createState, validateExport, makeExport, load, parseJson, questions,
    complete, count: state => state.answers.filter(complete).length });
})();
