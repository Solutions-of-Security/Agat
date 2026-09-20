import type { KnowledgeCollection } from "../types";
import { Icon } from "./Icon";

interface KnowledgeCollectionPickerProps {
  collections: KnowledgeCollection[];
  selectedIds: string[];
  onToggle: (collectionId: string) => void;
}

export function KnowledgeCollectionPicker({
  collections,
  selectedIds,
  onToggle,
}: KnowledgeCollectionPickerProps) {
  return (
    <fieldset className="knowledge-picker">
      <legend>Локальные знания <small>необязательно</small></legend>
      {selectedIds.filter((id) => !collections.some((collection) => collection.id === id)).map((id) => <p key={id}>Коллекция {id} недоступна. <button type="button" onClick={() => onToggle(id)}>Убрать из выбора</button></p>)}
      {collections.length === 0 ? (
        <div className="knowledge-picker__empty">
          <Icon name="knowledge" size={18} />
          <span>Коллекций пока нет. Создайте их в разделе Knowledge.</span>
        </div>
      ) : (
        <div className="knowledge-picker__grid">
          {collections.map((collection) => {
            const selected = selectedIds.includes(collection.id);
            const ready = collection.chunkCount > 0 && collection.embeddedChunks === collection.chunkCount;
            const state = collection.chunkCount === 0 ? "пустая" : ready ? "готова" : "индексация";
            return (
              <label className={selected ? "is-selected" : ""} key={collection.id}>
                <input
                  type="checkbox"
                  checked={selected}
                  onChange={() => onToggle(collection.id)}
                />
                <span className="knowledge-picker__check">{selected ? <Icon name="check" size={13} /> : "+"}</span>
                <span>
                  <strong>{collection.name}</strong>
                  <small>{collection.embeddingModel} · {collection.embeddedChunks}/{collection.chunkCount} chunks</small>
                </span>
                <i className={ready ? "is-ready" : ""}>{state}</i>
              </label>
            );
          })}
        </div>
      )}
    </fieldset>
  );
}
