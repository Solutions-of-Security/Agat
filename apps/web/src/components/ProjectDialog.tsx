import { useEffect, useRef, useState, type FormEvent } from "react";

import { Icon } from "./Icon";

interface ProjectDialogProps {
  open: boolean;
  busy: boolean;
  error: string | null;
  onClose: () => void;
  onSubmit: (name: string, id: string) => void;
}

export function ProjectDialog({ open, busy, error, onClose, onSubmit }: ProjectDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [name, setName] = useState("");
  const [id, setId] = useState("");

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      setName("");
      setId("");
      dialog.showModal();
    }
    if (!open && dialog.open) dialog.close();
  }, [open]);

  function submit(event: FormEvent) {
    event.preventDefault();
    onSubmit(name.trim(), id.trim().toLowerCase());
  }

  return (
    <dialog className="run-dialog project-dialog" ref={dialogRef} onCancel={onClose} onClose={onClose}>
      <form onSubmit={submit}>
        <div className="dialog-head">
          <div><h2>Новый проект</h2><p>Изолированное пространство агентов, процессов, запусков и credentials</p></div>
          <button className="icon-button" type="button" onClick={onClose} aria-label="Закрыть"><Icon name="close" /></button>
        </div>
        <label className="field"><span>Название</span><input required maxLength={100} autoFocus value={name} onChange={(event) => setName(event.target.value)} placeholder="Например, Аналитика" /></label>
        <label className="field"><span>ID проекта</span><input required pattern="[a-z0-9][a-z0-9_-]{0,63}" maxLength={64} value={id} onChange={(event) => setId(event.target.value.toLowerCase())} placeholder="analytics" /></label>
        <small className="field-hint">ID используется в токенах Keycloak (`agat_projects`) и не должен меняться.</small>
        {error ? <p className="form-error">{error}</p> : null}
        <div className="dialog-actions"><button className="button button--secondary" type="button" onClick={onClose}>Отмена</button><button className="button button--primary" type="submit" disabled={busy || !name || !id}>{busy ? "Создаём…" : "Создать"}</button></div>
      </form>
    </dialog>
  );
}
