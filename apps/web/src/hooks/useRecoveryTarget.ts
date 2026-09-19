import { useEffect, useRef, useState } from "react";
import { recoveryTarget } from "../scenarioPreflight";

export function useRecoveryTarget() {
  const [hash, setHash] = useState(() => typeof window === "undefined" ? "" : window.location.hash);
  useEffect(() => {
    const update = () => setHash(window.location.hash);
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);
  return recoveryTarget(hash);
}

export function useRecoveryFocus(id: string | null, revision?: unknown) {
  const focused = useRef<string | null>(null);
  useEffect(() => {
    if (!id) { focused.current = null; return; }
    if (focused.current === id) return;
    const element = document.getElementById(id);
    element?.scrollIntoView({ block: "center" });
    element?.focus({ preventScroll: true });
    if (element) focused.current = id;
  }, [id, revision]);
}
