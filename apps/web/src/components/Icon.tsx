import type { ReactNode, SVGProps } from "react";

export type IconName =
  | "agents"
  | "bell"
  | "box"
  | "back"
  | "check"
  | "chevron"
  | "clock"
  | "close"
  | "dashboard"
  | "diamond"
  | "down"
  | "dots"
  | "layers"
  | "list"
  | "layout"
  | "knowledge"
  | "menu"
  | "models"
  | "network"
  | "nodes"
  | "play"
  | "plug"
  | "plus"
  | "publish"
  | "repeat"
  | "redo"
  | "runs"
  | "search"
  | "shield"
  | "save"
  | "terminal"
  | "trash"
  | "undo"
  | "up"
  | "warning"
  | "workflow";

interface IconProps extends SVGProps<SVGSVGElement> {
  name: IconName;
  size?: number;
}

export function Icon({ name, size = 20, ...props }: IconProps) {
  const common = {
    width: size,
    height: size,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.7,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };

  const paths: Record<IconName, ReactNode> = {
    agents: <><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/></>,
    back: <><path d="m15 18-6-6 6-6"/><path d="M9 12h11"/></>,
    bell: <><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9"/><path d="M13.7 21a2 2 0 0 1-3.4 0"/></>,
    box: <><path d="m21 16-9 5-9-5V8l9-5 9 5v8Z"/><path d="m3.3 7.7 8.7 5 8.7-5M12 22V12.7"/></>,
    check: <path d="m5 12 4 4L19 6"/>,
    chevron: <path d="m9 18 6-6-6-6"/>,
    clock: <><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></>,
    close: <><path d="m6 6 12 12M18 6 6 18"/></>,
    dashboard: <><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></>,
    diamond: <path d="m12 2 10 10-10 10L2 12 12 2Z"/>,
    down: <path d="m6 9 6 6 6-6"/>,
    dots: <><circle cx="5" cy="12" r="1" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1" fill="currentColor" stroke="none"/><circle cx="19" cy="12" r="1" fill="currentColor" stroke="none"/></>,
    layers: <><path d="m12 2 9 5-9 5-9-5 9-5Z"/><path d="m3 12 9 5 9-5M3 17l9 5 9-5"/></>,
    list: <><path d="M8 6h13M8 12h13M8 18h13"/><path d="M3 6h.01M3 12h.01M3 18h.01"/></>,
    layout: <><rect x="3" y="4" width="7" height="6" rx="1"/><rect x="14" y="4" width="7" height="6" rx="1"/><rect x="8.5" y="15" width="7" height="5" rx="1"/><path d="M6.5 10v2.5h11V10M12 12.5V15"/></>,
    knowledge: <><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/><path d="M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/></>,
    menu: <><path d="M4 6h16M4 12h16M4 18h11"/></>,
    models: <><path d="m12 2 9 5-9 5-9-5 9-5Z"/><path d="m3 7v10l9 5 9-5V7M12 12v10"/></>,
    network: <><circle cx="5" cy="12" r="2.5"/><circle cx="19" cy="6" r="2.5"/><circle cx="19" cy="18" r="2.5"/><path d="m7.4 11 9.2-4M7.4 13l9.2 4"/></>,
    nodes: <><circle cx="12" cy="5" r="3"/><circle cx="5" cy="18" r="3"/><circle cx="19" cy="18" r="3"/><path d="m10.5 7.6-4 7M13.5 7.6l4 7M8 18h8"/></>,
    play: <path d="m8 5 11 7-11 7V5Z"/>,
    plug: <><path d="M12 22v-5M9 8V2M15 8V2"/><path d="M6 8h12v3a6 6 0 0 1-12 0V8Z"/></>,
    plus: <><path d="M12 5v14M5 12h14"/></>,
    publish: <><path d="M12 3v12M7 8l5-5 5 5"/><path d="M5 14v6h14v-6"/></>,
    repeat: <><path d="M17 2l4 4-4 4"/><path d="M3 11V9a3 3 0 0 1 3-3h15"/><path d="m7 22-4-4 4-4"/><path d="M21 13v2a3 3 0 0 1-3 3H3"/></>,
    redo: <><path d="m17 3 4 4-4 4"/><path d="M3 17v-3a7 7 0 0 1 7-7h11"/></>,
    runs: <path d="m7 4 13 8-13 8V4Z"/>,
    search: <><circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/></>,
    shield: <><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z"/><path d="m9 12 2 2 4-4"/></>,
    save: <><path d="M5 3h12l2 2v16H5V3Z"/><path d="M8 3v6h8V3M8 21v-7h8v7"/></>,
    terminal: <><path d="m4 17 6-6-6-6M12 19h8"/></>,
    trash: <><path d="M4 7h16M9 7V4h6v3M7 7l1 14h8l1-14M10 11v6M14 11v6"/></>,
    undo: <><path d="m7 3-4 4 4 4"/><path d="M21 17v-3a7 7 0 0 0-7-7H3"/></>,
    up: <path d="m6 15 6-6 6 6"/>,
    warning: <><path d="M10.3 3.7 2.5 17.2A2 2 0 0 0 4.2 20h15.6a2 2 0 0 0 1.7-2.8L13.7 3.7a2 2 0 0 0-3.4 0Z"/><path d="M12 9v4M12 17h.01"/></>,
    workflow: <><circle cx="6" cy="6" r="2"/><circle cx="18" cy="6" r="2"/><circle cx="12" cy="18" r="2"/><path d="M8 6h8M7 8l4 8M17 8l-4 8"/></>,
  };

  return <svg {...common} {...props}>{paths[name]}</svg>;
}
