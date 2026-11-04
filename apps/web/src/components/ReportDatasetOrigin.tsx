import type { ReportEvalImportLineage } from "../types";

export function ReportDatasetOrigin({ value }: { value: ReportEvalImportLineage | undefined }) {
  if (!value || value.profile !== "business-report-eval-import.v1" || value.purpose !== "development" || value.holdoutEligible !== false || value.reviewConfirmed !== true
    || !value.source?.runId || !value.source.artifactId || !/^[a-f0-9]{64}$/.test(value.source.sourceSha256)
    || !Number.isInteger(value.source.processVersion) || !Number.isInteger(value.source.decisionRevision)) return null;
  return <section className="report-origin" aria-label="Происхождение примера">
    <strong>Development · из отчёта</strong>
    <p>Версия процесса {value.source.processVersion} · решение №{value.source.decisionRevision} · {value.source.decision === "accepted" ? "Отчёт принят" : "Нужны исправления"}</p>
    <p>Эталон и критерии сохранены после явного review. Независимая оценка модели выполняется отдельно.</p>
    <a href={`#runs/${encodeURIComponent(value.source.runId)}`}>Исходный отчёт</a>
    <small>Копия примера хранится в dataset отдельно от исходного запуска.</small>
    <details><summary>Хэши и контекст происхождения</summary><p className="report-eval-hash">Artifact: {value.source.artifactId}</p>
      <p className="report-eval-hash">SHA-256 отчёта: {value.source.sourceSha256}</p><p className="report-eval-hash">SHA-256 входа: {value.source.inputSha256}</p>
      <p className="report-eval-hash">SHA-256 эталона: {value.referenceSha256}</p><p>Модель: {value.source.requestedModel ?? "auto"} · runtime: {value.source.runtimeProfile}</p></details>
  </section>;
}
