import { useEffect, useRef, type FormEvent } from "react";

import { Icon } from "./Icon";

interface AdminTokenDialogProps {
  open: boolean;
  onCancel: () => void;
  onSubmit: (token: string) => void;
}

export function AdminTokenDialog({ open, onCancel, onSubmit }: AdminTokenDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const formRef = useRef<HTMLFormElement>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      formRef.current?.reset();
      dialog.showModal();
    }
    if (!open && dialog.open) dialog.close();
  }, [open]);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const token = String(new FormData(event.currentTarget).get("token") ?? "").trim();
    if (token) onSubmit(token);
  }

  return (
    <dialog className="run-dialog admin-token-dialog" ref={dialogRef} onCancel={onCancel} onClose={onCancel}>
      <form ref={formRef} onSubmit={submit}>
        <div className="dialog-head">
          <div><h2>Доступ к защищённым данным</h2><p>Введите admin token локального coordinator</p></div>
          <button className="icon-button" type="button" onClick={onCancel} aria-label="Закрыть">
            <Icon name="close" />
          </button>
        </div>
        <div className="admin-token-dialog__notice">
          <Icon name="shield" size={22} />
          <span>
            <strong>Trace содержит точные входы и результаты</strong>
            <small>Токен сохраняется только в localStorage этого браузера и отправляется coordinator в HTTP-заголовке.</small>
          </span>
        </div>
        <label className="field">
          <span>Admin token</span>
          <input name="token" type="password" required minLength={16} autoComplete="off" autoFocus />
        </label>
        <div className="dialog-actions">
          <button className="button button--secondary" type="button" onClick={onCancel}>Отмена</button>
          <button className="button button--primary" type="submit"><Icon name="shield" size={16} />Продолжить</button>
        </div>
      </form>
    </dialog>
  );
}
