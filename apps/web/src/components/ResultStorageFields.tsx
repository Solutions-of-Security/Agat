import type { ResultDestination } from "../types";
import { Icon } from "./Icon";

interface ResultStorageFieldsProps {
  destination: ResultDestination;
  artifactPath: string;
  onDestinationChange: (destination: ResultDestination) => void;
  onArtifactPathChange: (path: string) => void;
}

export function ResultStorageFields({
  destination,
  artifactPath,
  onDestinationChange,
  onArtifactPathChange,
}: ResultStorageFieldsProps) {
  return (
    <fieldset className="result-storage">
      <legend>Куда сохранить результат</legend>
      <div className="result-storage__options">
        <label className={destination === "history" ? "is-selected" : ""}>
          <input
            type="radio"
            name="resultDestination"
            value="history"
            checked={destination === "history"}
            onChange={() => onDestinationChange("history")}
          />
          <Icon name="list" size={18} />
          <span>
            <strong>В журнал АГАТ</strong>
            <small>Вход, логи и output хранятся в SQLite; результат можно скачать из запуска.</small>
          </span>
        </label>
        <label className={destination === "artifacts" ? "is-selected" : ""}>
          <input
            type="radio"
            name="resultDestination"
            value="artifacts"
            checked={destination === "artifacts"}
            onChange={() => onDestinationChange("artifacts")}
          />
          <Icon name="save" size={18} />
          <span>
            <strong>Журнал + Artifact Store</strong>
            <small>Каждый этап и финальный результат сохраняются файлами в постоянном хранилище.</small>
          </span>
        </label>
      </div>
      {destination === "artifacts" ? (
        <label className="field result-storage__path">
          <span>Каталог внутри Artifact Store · необязательно</span>
          <input
            value={artifactPath}
            maxLength={180}
            placeholder="Например, reports/releases"
            onChange={(event) => onArtifactPathChange(event.target.value)}
          />
          <small>Используется относительный путь; к нему автоматически добавится ID запуска.</small>
        </label>
      ) : null}
    </fieldset>
  );
}
