import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import { initializeAuth } from "./lib/auth";
import "./styles.css";

const root = createRoot(document.getElementById("root")!);

void initializeAuth()
  .then(() => root.render(
    <StrictMode>
      <App />
    </StrictMode>,
  ))
  .catch((error: unknown) => root.render(
    <div className="boot-screen">
      <div className="boot-mark" />
      <strong>Не удалось выполнить вход</strong>
      <p>{error instanceof Error ? error.message : "Ошибка OIDC"}</p>
      <button className="button button--secondary" type="button" onClick={() => window.location.reload()}>Повторить</button>
    </div>,
  ));

if (import.meta.env.PROD && "serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    void navigator.serviceWorker.register("/sw.js");
  });
}
