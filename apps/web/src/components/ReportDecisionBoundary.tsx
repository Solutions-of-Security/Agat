import { Component, type ReactNode } from "react";

export class ReportDecisionBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    return this.state.failed ? <div className="report-review" role="alert">
      <p>Не удалось загрузить действия с отчётом. Чтение и скачивание доступны.</p>
      <button className="button button--secondary" type="button" onClick={() => window.location.reload()}>Перезагрузить страницу</button>
    </div> : this.props.children;
  }
}
