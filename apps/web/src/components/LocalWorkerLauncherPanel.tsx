import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";

import { api } from "../lib/api";
import type { ComputeNode, LocalWorkerLauncherSnapshot, LocalWorkerPool } from "../types";
import { Icon } from "./Icon";

const poolStatusCopy: Record<LocalWorkerPool["status"], string> = {
  starting: "Запускается",
  ready: "Готов",
  degraded: "Частично готов",
  stopped: "Остановлен",
};

function modelSize(value: number | null): string {
  if (!value) return "размер не указан";
  const gib = value / (1024 ** 3);
  return gib >= 1 ? `${gib.toFixed(gib >= 10 ? 0 : 1)} ГБ` : `${Math.round(value / (1024 ** 2))} МБ`;
}

interface LocalWorkerLauncherPanelProps {
  nodes: ComputeNode[];
  knownModels: string[];
}

export function LocalWorkerLauncherPanel({ nodes, knownModels }: LocalWorkerLauncherPanelProps) {
  const [snapshot, setSnapshot] = useState<LocalWorkerLauncherSnapshot | null>(null);
  const [name, setName] = useState("");
  const [model, setModel] = useState("");
  const [workers, setWorkers] = useState(1);
  const [concurrency, setConcurrency] = useState(1);
  const [webEnabled, setWebEnabled] = useState(true);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const defaultsApplied = useRef(false);
  const fallbackModel = knownModels[0] ?? "";

  const refresh = useCallback(async (signal?: AbortSignal) => {
    try {
      const next = await api.localWorkers(signal);
      setSnapshot(next);
      setError(null);
      if (!defaultsApplied.current) {
        defaultsApplied.current = true;
        setWebEnabled(next.defaultWebEnabled);
        setModel(next.models[0]?.name ?? fallbackModel);
      }
    } catch (requestError) {
      if (requestError instanceof DOMException && requestError.name === "AbortError") return;
      setError(requestError instanceof Error ? requestError.message : "Не удалось получить состояние launcher");
    }
  }, [fallbackModel]);

  useEffect(() => {
    const controller = new AbortController();
    void refresh(controller.signal);
    const interval = window.setInterval(() => void refresh(), 5_000);
    return () => {
      controller.abort();
      window.clearInterval(interval);
    };
  }, [refresh]);

  const modelOptions = useMemo(() => {
    const discovered = snapshot?.models.map((item) => item.name) ?? [];
    return [...new Set([...discovered, ...knownModels])].sort((left, right) => left.localeCompare(right));
  }, [knownModels, snapshot?.models]);

  async function launch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusyKey("launch");
    setError(null);
    try {
      await api.launchLocalWorkers({ name, model, workers, concurrency, webEnabled });
      setName("");
      await refresh();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Не удалось запустить workers");
    } finally {
      setBusyKey(null);
    }
  }

  async function setPoolRunning(pool: LocalWorkerPool, running: boolean) {
    setBusyKey(pool.id);
    setConfirmDeleteId(null);
    setError(null);
    try {
      if (running) await api.startLocalWorkerPool(pool.id);
      else await api.stopLocalWorkerPool(pool.id);
      await refresh();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Не удалось изменить worker-пул");
    } finally {
      setBusyKey(null);
    }
  }

  async function deletePool(pool: LocalWorkerPool) {
    setBusyKey(pool.id);
    setError(null);
    try {
      await api.deleteLocalWorkerPool(pool.id);
      setConfirmDeleteId(null);
      await refresh();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Не удалось удалить worker-пул");
    } finally {
      setBusyKey(null);
    }
  }

  const maxWorkers = snapshot?.maxWorkersPerLaunch ?? 8;
  const available = snapshot?.available ?? false;

  return (
    <section className="local-launcher" aria-labelledby="local-launcher-title">
      <header className="local-launcher__head">
        <div className="local-launcher__icon"><Icon name="terminal" size={22} /></div>
        <div>
          <span className="eyebrow">DOCKER DESKTOP · LOCAL K8S</span>
          <h2 id="local-launcher-title">Запустить локальные workers</h2>
          <p>Каждый worker регистрируется отдельно, но использует выбранную локальную модель. Общий планировщик всё равно может исполнять задачи последовательно.</p>
        </div>
        <span className={`readiness readiness--${available ? "ready" : "blocked"}`}>
          <i />{snapshot ? available ? "Launcher готов" : "Недоступен" : "Проверяем"}
        </span>
      </header>

      {snapshot && !snapshot.available ? (
        <div className="local-launcher__unavailable">
          <Icon name="warning" size={20} />
          <div><strong>Запуск из интерфейса недоступен</strong><p>{snapshot.reason}</p></div>
        </div>
      ) : (
        <form className="local-launcher__form" onSubmit={(event) => void launch(event)}>
          <label className="field local-launcher__model">
            <span>Модель</span>
            <input
              list="local-worker-models"
              value={model}
              onChange={(event) => setModel(event.target.value)}
              placeholder="Например, llama3.2:latest"
              required
              autoComplete="off"
            />
            <datalist id="local-worker-models">
              {modelOptions.map((item) => <option value={item} key={item} />)}
            </datalist>
            <small>{snapshot?.models.find((item) => item.name === model)
              ? `Установлена локально · ${modelSize(snapshot.models.find((item) => item.name === model)?.sizeBytes ?? null)}`
              : "Можно выбрать обнаруженную модель или ввести точное имя вручную"}</small>
          </label>
          <label className="field">
            <span>Workers</span>
            <input type="number" min={1} max={maxWorkers} value={workers} onChange={(event) => setWorkers(Number.isFinite(event.target.valueAsNumber) ? event.target.valueAsNumber : 1)} required />
            <small>От 1 до {maxWorkers} отдельных экземпляров</small>
          </label>
          <label className="field">
            <span>Слотов на worker</span>
            <input type="number" min={1} max={8} value={concurrency} onChange={(event) => setConcurrency(Number.isFinite(event.target.valueAsNumber) ? event.target.valueAsNumber : 1)} required />
            <small>Для одной локальной модели обычно 1</small>
          </label>
          <label className="field local-launcher__name">
            <span>Название пула · необязательно</span>
            <input value={name} maxLength={80} onChange={(event) => setName(event.target.value)} placeholder={`${model || "Модель"} · локальные workers`} />
          </label>
          <label className="local-launcher__check">
            <input type="checkbox" checked={webEnabled} onChange={(event) => setWebEnabled(event.target.checked)} />
            <span><strong>Web-инструменты</strong><small>Подключить web_search и web_fetch через локальный SearXNG</small></span>
          </label>
          <button className="button button--primary local-launcher__submit" type="submit" disabled={!snapshot?.available || busyKey !== null || !model.trim()}>
            <Icon name="play" size={16} />{busyKey === "launch" ? "Запускаем…" : `Запустить ${workers} ${workers === 1 ? "worker" : "workers"}`}
          </button>
        </form>
      )}

      {snapshot?.modelDiscoveryError ? (
        <p className="local-launcher__warning"><Icon name="warning" size={15} />{snapshot.modelDiscoveryError}. Точное имя модели всё ещё можно ввести вручную.</p>
      ) : null}
      {error ? <p className="local-launcher__error" role="alert">{error}</p> : null}

      {snapshot?.pools.length ? (
        <div className="local-pools" aria-label="Локальные worker-пулы">
          <div className="local-pools__title"><span>Управляемые пулы</span><strong>{snapshot.pools.length}</strong></div>
          {snapshot.pools.map((pool) => {
            const poolNodes = nodes.filter((node) => node.labels.pool === pool.id);
            const onlineNodes = poolNodes.filter((node) => node.status === "online").length;
            return (
              <article className={`local-pool local-pool--${pool.status}`} key={pool.id}>
                <div className="local-pool__status"><i /><span>{poolStatusCopy[pool.status]}</span></div>
                <div className="local-pool__identity">
                  <h3>{pool.name}</h3>
                  <code>{pool.model}</code>
                </div>
                <dl>
                  <div><dt>Kubernetes</dt><dd>{pool.readyWorkers} / {pool.workers} ready</dd></div>
                  <div><dt>Heartbeat</dt><dd>{onlineNodes} / {pool.workers} online</dd></div>
                  <div><dt>Параллельность</dt><dd>{pool.workers} × {pool.concurrency}</dd></div>
                </dl>
                <div className="local-pool__workers">
                  {pool.workerNames.map((workerName) => <span className="mono" key={workerName}>{workerName}</span>)}
                </div>
                <div className={`local-pool__actions${confirmDeleteId === pool.id ? " local-pool__actions--confirm" : ""}`}>
                  {confirmDeleteId === pool.id ? (
                    <>
                      <button className="button local-pool__delete-confirm" type="button" disabled={busyKey !== null} onClick={() => void deletePool(pool)}>
                        <Icon name="trash" size={14} />{busyKey === pool.id ? "Удаляем…" : "Удалить пул"}
                      </button>
                      <button className="icon-button" type="button" aria-label="Отменить удаление" onClick={() => setConfirmDeleteId(null)}><Icon name="close" size={15} /></button>
                    </>
                  ) : (
                    <>
                      <button
                        className={`button ${pool.status === "stopped" ? "button--primary" : "button--secondary"}`}
                        type="button"
                        disabled={busyKey !== null}
                        onClick={() => void setPoolRunning(pool, pool.status === "stopped")}
                      >
                        <Icon name={pool.status === "stopped" ? "play" : "close"} size={15} />
                        {busyKey === pool.id ? "Меняем…" : pool.status === "stopped" ? "Запустить" : "Остановить"}
                      </button>
                      <button className="icon-button local-pool__trash" type="button" aria-label={`Удалить пул ${pool.name}`} disabled={busyKey !== null} onClick={() => setConfirmDeleteId(pool.id)}>
                        <Icon name="trash" size={15} />
                      </button>
                    </>
                  )}
                </div>
              </article>
            );
          })}
        </div>
      ) : null}
    </section>
  );
}
