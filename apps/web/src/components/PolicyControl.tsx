import type { SchedulerMode } from "../types";

const modes: Array<{ value: SchedulerMode; label: string; description: string }> = [
  { value: "sequential", label: "Последовательно", description: "Один этап во всём контуре" },
  { value: "parallel", label: "Параллельно", description: "До лимита каждого узла" },
  { value: "auto", label: "Авто", description: "С учётом загрузки и батареи" },
];

interface PolicyControlProps {
  value: SchedulerMode;
  disabled: boolean;
  onChange: (mode: SchedulerMode) => void;
}

export function PolicyControl({ value, disabled, onChange }: PolicyControlProps) {
  return (
    <div className="policy" aria-labelledby="policy-title">
      <h3 id="policy-title">Политика ресурсов</h3>
      <div className="policy__options">
        {modes.map((mode) => (
          <label className={value === mode.value ? "is-selected" : ""} key={mode.value}>
            <input
              type="radio"
              name="scheduler-mode"
              value={mode.value}
              checked={value === mode.value}
              disabled={disabled}
              onChange={() => onChange(mode.value)}
            />
            <span className="radio-mark" />
            <span><strong>{mode.label}</strong><small>{mode.description}</small></span>
          </label>
        ))}
      </div>
    </div>
  );
}
