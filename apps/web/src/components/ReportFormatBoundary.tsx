import React, { Component, type ReactNode } from "react";
import type { KnowledgeSource } from "../types";
import { KnowledgeLinkedOutput } from "./KnowledgeSources";

/** Keeps the original result available even if the deferred renderer cannot load. */
export class ReportFormatBoundary extends Component<{ text: string; sources: KnowledgeSource[]; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidUpdate(previous: Readonly<{ text: string }>) {
    if (previous.text !== this.props.text && this.state.failed) this.setState({ failed: false });
  }
  render() {
    return this.state.failed ? <><p role="alert">Не удалось отформатировать результат. Исходный текст и скачивание сохранены.</p>
      <KnowledgeLinkedOutput text={this.props.text} sources={this.props.sources} /></> : this.props.children;
  }
}
