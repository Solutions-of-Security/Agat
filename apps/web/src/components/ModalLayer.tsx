import { useRef, type ReactNode } from "react";

import { useModalFocus } from "../hooks/useModalFocus";

interface ModalLayerProps {
  className: string;
  id?: string;
  label?: string;
  labelledBy?: string;
  onClose: () => void;
  children: ReactNode;
}

export function ModalLayer({ className, id, label, labelledBy, onClose, children }: ModalLayerProps) {
  const dialogRef = useRef<HTMLDivElement>(null);
  useModalFocus(true, dialogRef, onClose);

  return (
    <div
      className={className}
      id={id}
      ref={dialogRef}
      role="dialog"
      aria-modal="true"
      aria-label={label}
      aria-labelledby={labelledBy}
      tabIndex={-1}
    >
      {children}
    </div>
  );
}
