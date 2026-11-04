import { useState, useSyncExternalStore, type FormEvent, type ReactNode } from "react";
import { localAuthEnabled, localSessionAuthenticated, loginLocal, subscribeLocalSession } from "../lib/auth";
import { Brand } from "./Brand";

export function LocalSessionGate({ children }: { children: ReactNode }) {
  const authenticated = useSyncExternalStore(subscribeLocalSession, localSessionAuthenticated);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  if (!localAuthEnabled() || authenticated) return children;
  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const token = String(new FormData(form).get("personal-token") ?? "").trim();
    setError(null); setPending(true);
    try { await loginLocal(token); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "Ошибка входа"); }
    finally { form.reset(); setPending(false); }
  }
  return <main className="local-login">
    <form className="local-login__card" onSubmit={event => void login(event)}>
      <Brand />
      <h1>Вход в Агат</h1>
      <p>Используйте персональный ключ, выданный администратором.</p>
      <label className="field"><span>Персональный ключ</span><input name="personal-token" type="password" autoComplete="off" required maxLength={128} autoFocus disabled={pending} /></label>
      <p className="form-hint">Ключ обменивается на защищённую сессию и не сохраняется в хранилище браузера.</p>
      {error ? <p role="alert">{error}</p> : null}
      <button className="button button--primary" type="submit" disabled={pending}>{pending ? "Входим…" : "Войти"}</button>
    </form>
  </main>;
}
