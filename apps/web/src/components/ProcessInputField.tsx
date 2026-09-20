import { useEffect, useState } from "react";

import { api } from "../lib/api";

export function ProcessInputField({ processId, version, open, busy, onReadyChange }: {
  processId: string | null;
  version: number | "draft";
  open: boolean;
  busy: boolean;
  onReadyChange: (ready: boolean) => void;
}) {
  const [input, setInput] = useState("");
  const [hasTemplate, setHasTemplate] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    onReadyChange(false);
    if (!open || !processId) return;
    setLoading(true);
    setError(null);
    setInput("");
    setHasTemplate(false);
    void api.processVersion(processId, version).then((document) => {
      if (cancelled) return;
      const template = document.graph.nodes.find((node) => node.type === "start")?.config.inputTemplate ?? "";
      setInput(template);
      setHasTemplate(Boolean(template));
      setLoading(false);
      onReadyChange(true);
    }).catch(() => {
      if (cancelled) return;
      setLoading(false);
      setError("Не удалось загрузить входные данные этой версии процесса.");
    });
    return () => { cancelled = true; };
  }, [processId, version, open, attempt, onReadyChange]);

  return (
    <>
      <label className="field">
        <span>Входные данные</span>
        <textarea
          name="input"
          required
          rows={hasTemplate ? 16 : 7}
          maxLength={100_000}
          placeholder="Задача, исходные данные, ограничения и критерии результата"
          value={input}
          onChange={(event) => setInput(event.target.value)}
          disabled={busy || loading || Boolean(error)}
          aria-describedby="process-input-help"
        />
        <small id="process-input-help">
          {loading ? "Загружаем шаблон входных данных…" : hasTemplate
            ? "Подставлен шаблон процесса. Заполните отмеченные параметры и проверьте значения по умолчанию."
            : "Укажите задачу и параметры для этого запуска."}
        </small>
      </label>
      {error ? <div role="alert">
        <p className="form-error">{error}</p>
        <button className="button button--secondary" type="button" disabled={busy} onClick={() => setAttempt((value) => value + 1)}>Повторить загрузку входных данных</button>
      </div> : null}
    </>
  );
}
