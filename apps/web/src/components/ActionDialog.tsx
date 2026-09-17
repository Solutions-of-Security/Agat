import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";

import { useModalFocus } from "../hooks/useModalFocus";
import { Icon } from "./Icon";

export type ActionDialogTone = "accent" | "warning" | "danger";

export interface ActionDialogInput {
  label: string;
  placeholder?: string;
  defaultValue?: string;
  hint?: string;
  required?: boolean;
  requiredMessage?: string;
  maxLength?: number;
}

export interface ActionDialogRequest {
  title: string;
  description: string;
  confirmLabel: string;
  cancelLabel?: string;
  tone?: ActionDialogTone;
  subject?: string;
  subjectLabel?: string;
  impact: string;
  recovery: string;
  input?: ActionDialogInput;
}

export interface ActionDialogResult {
  confirmed: boolean;
  value: string | null;
}

interface ActiveActionDialog {
  id: number;
  request: ActionDialogRequest;
}

type ActionDialogRequester = (request: ActionDialogRequest) => Promise<ActionDialogResult>;

const ActionDialogContext = createContext<ActionDialogRequester | null>(null);

export function validateActionDialogValue(value: string, input?: ActionDialogInput): string | null {
  if (!input) return null;
  const normalized = value.trim();
  if (input.required && !normalized) return input.requiredMessage ?? `Заполните поле «${input.label}»`;
  const maxLength = input.maxLength ?? 1_000;
  if (normalized.length > maxLength) return `Не больше ${maxLength} символов`;
  return null;
}

function ActionDialog({
  active,
  onCancel,
  onConfirm,
}: {
  active: ActiveActionDialog | null;
  onCancel: () => void;
  onConfirm: (value: string | null) => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const titleId = useId();
  const descriptionId = useId();
  const inputId = useId();
  const [value, setValue] = useState("");
  const [validationError, setValidationError] = useState<string | null>(null);
  const open = active !== null;

  useModalFocus(open, dialogRef, onCancel);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  useEffect(() => {
    setValue(active?.request.input?.defaultValue ?? "");
    setValidationError(null);
  }, [active?.id, active?.request.input?.defaultValue]);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!active) return;
    const error = validateActionDialogValue(value, active.request.input);
    if (error) {
      setValidationError(error);
      inputRef.current?.focus();
      return;
    }
    onConfirm(active.request.input ? value.trim() || null : null);
  }

  const request = active?.request;
  const tone = request?.tone ?? "danger";
  const errorId = `${inputId}-error`;

  return (
    <dialog
      className={`action-dialog action-dialog--${tone}`}
      ref={dialogRef}
      aria-labelledby={titleId}
      aria-describedby={descriptionId}
      onCancel={(event) => { event.preventDefault(); onCancel(); }}
    >
      {request ? (
        <form onSubmit={submit}>
          <header className="action-dialog__header">
            <span className="action-dialog__icon"><Icon name={tone === "accent" ? "publish" : "warning"} size={22} /></span>
            <div>
              <h2 id={titleId}>{request.title}</h2>
              <p id={descriptionId}>{request.description}</p>
            </div>
            <button className="icon-button" type="button" aria-label="Закрыть" onClick={onCancel}>
              <Icon name="close" size={18} />
            </button>
          </header>

          <div className="action-dialog__body">
            {request.subject ? (
              <div className="action-dialog__subject">
                <span>{request.subjectLabel ?? "Объект"}</span>
                <strong>{request.subject}</strong>
              </div>
            ) : null}

            <div className="action-dialog__consequences">
              <section>
                <span><Icon name="warning" size={17} /></span>
                <div><h3>Что изменится</h3><p>{request.impact}</p></div>
              </section>
              <section>
                <span><Icon name="repeat" size={17} /></span>
                <div><h3>Можно ли восстановить</h3><p>{request.recovery}</p></div>
              </section>
            </div>

            {request.input ? (
              <label className="action-dialog__field" htmlFor={inputId}>
                <span>{request.input.label}{request.input.required ? <strong>обязательно</strong> : null}</span>
                <textarea
                  ref={inputRef}
                  id={inputId}
                  data-autofocus
                  maxLength={request.input.maxLength ?? 1_000}
                  placeholder={request.input.placeholder}
                  value={value}
                  aria-invalid={validationError ? true : undefined}
                  aria-describedby={validationError ? errorId : undefined}
                  onChange={(event) => { setValue(event.target.value); if (validationError) setValidationError(null); }}
                />
                {request.input.hint ? <small>{request.input.hint}</small> : null}
                {validationError ? <em id={errorId} role="alert">{validationError}</em> : null}
              </label>
            ) : null}
          </div>

          <footer className="action-dialog__actions">
            <button className="button button--secondary" type="button" onClick={onCancel}>{request.cancelLabel ?? "Отмена"}</button>
            <button className={`button ${tone === "accent" ? "button--primary" : tone === "warning" ? "button--warning" : "button--danger"}`} type="submit">
              {request.confirmLabel}
            </button>
          </footer>
        </form>
      ) : null}
    </dialog>
  );
}

export function ActionDialogProvider({ children }: { children: ReactNode }) {
  const [active, setActive] = useState<ActiveActionDialog | null>(null);
  const resolverRef = useRef<((result: ActionDialogResult) => void) | null>(null);
  const nextIdRef = useRef(1);

  const settle = useCallback((result: ActionDialogResult) => {
    const resolve = resolverRef.current;
    resolverRef.current = null;
    setActive(null);
    resolve?.(result);
  }, []);

  const requestAction = useCallback<ActionDialogRequester>((request) => new Promise((resolve) => {
    resolverRef.current?.({ confirmed: false, value: null });
    resolverRef.current = resolve;
    setActive({ id: nextIdRef.current++, request });
  }), []);

  useEffect(() => () => {
    resolverRef.current?.({ confirmed: false, value: null });
    resolverRef.current = null;
  }, []);

  return (
    <ActionDialogContext.Provider value={requestAction}>
      {children}
      <ActionDialog
        active={active}
        onCancel={() => settle({ confirmed: false, value: null })}
        onConfirm={(value) => settle({ confirmed: true, value })}
      />
    </ActionDialogContext.Provider>
  );
}

export function useActionDialog(): ActionDialogRequester {
  const requestAction = useContext(ActionDialogContext);
  if (!requestAction) throw new Error("useActionDialog должен использоваться внутри ActionDialogProvider");
  return requestAction;
}
