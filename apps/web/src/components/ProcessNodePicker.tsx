import { useEffect, useMemo, useRef, useState } from "react";

import type { ProcessNodeType } from "../types";
import { Icon, type IconName } from "./Icon";

export interface ProcessNodeTool {
  type: ProcessNodeType;
  label: string;
  description: string;
  category: "trigger" | "agent" | "data" | "flow" | "output";
  icon: IconName;
  keywords: string[];
}

export const processNodeTools: ProcessNodeTool[] = [
  {
    type: "start",
    label: "Старт",
    description: "Получить данные от ручного запуска или триггера",
    category: "trigger",
    icon: "play",
    keywords: ["начало", "manual", "trigger"],
  },
  {
    type: "signal",
    label: "Ожидание события",
    description: "Остановить поток до адресного внешнего события",
    category: "trigger",
    icon: "network",
    keywords: ["signal", "event", "callback", "корреляция"],
  },
  {
    type: "agent",
    label: "Агент",
    description: "Выполнить задачу выбранным агентом",
    category: "agent",
    icon: "agents",
    keywords: ["модель", "llm", "worker"],
  },
  {
    type: "http",
    label: "HTTP-запрос",
    description: "Получить или отправить данные во внешнюю систему",
    category: "data",
    icon: "terminal",
    keywords: ["api", "rest", "webhook", "request"],
  },
  {
    type: "transform",
    label: "Преобразование",
    description: "Собрать новый текст из входа и JSON",
    category: "data",
    icon: "layers",
    keywords: ["template", "set", "map", "json"],
  },
  {
    type: "wait",
    label: "Ожидание",
    description: "Продолжить процесс через заданное время",
    category: "flow",
    icon: "clock",
    keywords: ["delay", "sleep", "timer"],
  },
  {
    type: "approval",
    label: "Подтверждение",
    description: "Получить решение или дополнительные данные через форму",
    category: "flow",
    icon: "shield",
    keywords: ["human", "approve", "gate", "форма", "ввод", "согласование", "дополнить"],
  },
  {
    type: "condition",
    label: "Условие",
    description: "Разделить поток на ветки «да» и «нет»",
    category: "flow",
    icon: "diamond",
    keywords: ["if", "ветка", "branch"],
  },
  {
    type: "loop",
    label: "Цикл",
    description: "Повторять фрагмент с безопасным лимитом",
    category: "flow",
    icon: "repeat",
    keywords: ["повтор", "loop", "итерация"],
  },
  {
    type: "parallel_fork",
    label: "Параллельные ветки",
    description: "Запустить несколько веток одновременно",
    category: "flow",
    icon: "network",
    keywords: ["fork", "parallel", "gateway", "разделить"],
  },
  {
    type: "parallel_join",
    label: "Объединение веток",
    description: "Дождаться завершения всех параллельных веток",
    category: "flow",
    icon: "diamond",
    keywords: ["join", "parallel", "gateway", "объединить"],
  },
  {
    type: "subprocess",
    label: "Вложенный процесс",
    description: "Вызвать опубликованную версию другого процесса",
    category: "flow",
    icon: "workflow",
    keywords: ["subprocess", "call activity", "шаблон", "вложенный"],
  },
  {
    type: "artifact",
    label: "Артефакт",
    description: "Сохранить результат процесса в файл",
    category: "output",
    icon: "box",
    keywords: ["file", "save", "result", "output"],
  },
  {
    type: "end",
    label: "Завершение",
    description: "Закончить выбранную ветку процесса",
    category: "flow",
    icon: "close",
    keywords: ["конец", "stop", "finish"],
  },
];

const categoryLabels: Record<ProcessNodeTool["category"], string> = {
  trigger: "Триггеры",
  agent: "Агенты",
  data: "Данные и интеграции",
  flow: "Управление потоком",
  output: "Результаты",
};

interface ProcessNodePickerProps {
  open: boolean;
  position: { x: number; y: number } | null;
  excludedTypes?: ProcessNodeType[];
  onSelect: (type: ProcessNodeType) => void;
  onClose: () => void;
}

export function ProcessNodePicker({
  open,
  position,
  excludedTypes = [],
  onSelect,
  onClose,
}: ProcessNodePickerProps) {
  const [query, setQuery] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    setQuery("");
    window.requestAnimationFrame(() => inputRef.current?.focus());
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose, open]);

  const excluded = useMemo(() => new Set(excludedTypes), [excludedTypes]);
  const normalizedQuery = query.trim().toLocaleLowerCase("ru");
  const filtered = useMemo(() => processNodeTools.filter((tool) => {
    if (excluded.has(tool.type)) return false;
    if (!normalizedQuery) return true;
    return [tool.label, tool.description, ...tool.keywords]
      .some((value) => value.toLocaleLowerCase("ru").includes(normalizedQuery));
  }), [excluded, normalizedQuery]);

  if (!open || !position) return null;

  const style = {
    left: Math.min(Math.max(12, position.x), Math.max(12, window.innerWidth - 348)),
    top: Math.min(Math.max(12, position.y), Math.max(12, window.innerHeight - 480)),
  };

  return (
    <>
      <button className="process-node-picker__backdrop" type="button" aria-label="Закрыть каталог шагов" onClick={onClose} />
      <section className="process-node-picker" style={style} aria-label="Добавление шага">
        <header>
          <div className="process-node-picker__search">
            <Icon name="search" size={16} />
            <input
              ref={inputRef}
              value={query}
              placeholder="Найти шаг…"
              aria-label="Поиск шага"
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && filtered[0]) onSelect(filtered[0].type);
              }}
            />
            <kbd>ESC</kbd>
          </div>
        </header>
        <div className="process-node-picker__body">
          {filtered.length === 0 ? (
            <div className="process-node-picker__empty">Шаги не найдены</div>
          ) : (["trigger", "agent", "data", "flow", "output"] as const).map((category) => {
            const tools = filtered.filter((tool) => tool.category === category);
            if (tools.length === 0) return null;
            return (
              <div className="process-node-picker__group" key={category}>
                <h3>{categoryLabels[category]}</h3>
                {tools.map((tool) => (
                  <button type="button" key={tool.type} onClick={() => onSelect(tool.type)}>
                    <span className={`process-node-picker__icon process-node-picker__icon--${tool.type}`}>
                      <Icon name={tool.icon} size={18} />
                    </span>
                    <span><strong>{tool.label}</strong><small>{tool.description}</small></span>
                    <Icon name="chevron" size={14} />
                  </button>
                ))}
              </div>
            );
          })}
        </div>
      </section>
    </>
  );
}
