import { useMemo, useState } from "react";
import { createPortal } from "react-dom";

import { notificationStorageKey, unreadNotificationIds, type AppNotification } from "../notifications";
import { Icon } from "./Icon";
import { ModalLayer } from "./ModalLayer";

interface NotificationCenterProps {
  notifications: AppNotification[];
  projectId: string;
  onOpen: (notification: AppNotification) => void;
  onOpenApprovals: () => void;
}

function readSeenIds(storageKey: string): Set<string> {
  try {
    const value = JSON.parse(window.localStorage.getItem(storageKey) ?? "[]");
    return new Set(Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : []);
  } catch {
    return new Set();
  }
}

function shortDate(value: string): string {
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

export function NotificationCenter({ notifications, projectId, onOpen, onOpenApprovals }: NotificationCenterProps) {
  const storageKey = notificationStorageKey(projectId);
  const [open, setOpen] = useState(false);
  const [seenIds, setSeenIds] = useState<Set<string>>(() => readSeenIds(storageKey));
  const unreadIds = useMemo(() => unreadNotificationIds(notifications, seenIds), [notifications, seenIds]);
  const unreadSet = useMemo(() => new Set(unreadIds), [unreadIds]);
  const unreadCount = unreadIds.length;

  function persist(next: Set<string>) {
    setSeenIds(next);
    try {
      window.localStorage.setItem(storageKey, JSON.stringify([...next].slice(-200)));
    } catch {
      // Private browsing or storage policy may reject persistence; session state still works.
    }
  }

  function markRead(notification: AppNotification) {
    const next = new Set(seenIds);
    next.add(notification.id);
    persist(next);
  }

  function markAllRead() {
    const next = new Set(seenIds);
    notifications.forEach((notification) => next.add(notification.id));
    persist(next);
  }

  function openNotification(notification: AppNotification) {
    markRead(notification);
    setOpen(false);
    onOpen(notification);
  }

  return (
    <>
      <button
        className="icon-button topbar__notifications"
        type="button"
        aria-label={unreadCount > 0 ? `Уведомления: ${unreadCount} непрочитанных` : "Открыть уведомления"}
        aria-expanded={open}
        aria-controls="notification-drawer"
        title="Уведомления"
        onClick={() => setOpen(true)}
      >
        <Icon name="bell" size={20} />
        {unreadCount > 0 ? <span className="notification-count">{unreadCount}</span> : null}
      </button>
      <span className="sr-only" role="status" aria-live="polite">{unreadCount > 0 ? `${unreadCount} непрочитанных уведомлений` : "Новых уведомлений нет"}</span>

      {open ? createPortal((
        <div className="notification-layer">
          <button className="notification-backdrop" type="button" tabIndex={-1} aria-label="Закрыть уведомления" onClick={() => setOpen(false)} />
          <ModalLayer className="notification-drawer" id="notification-drawer" labelledBy="notification-title" onClose={() => setOpen(false)}>
            <header>
              <div><h2 id="notification-title">Уведомления</h2><p>{unreadCount > 0 ? `${unreadCount} требуют просмотра` : "Новых уведомлений нет"}</p></div>
              <button className="icon-button" type="button" data-autofocus aria-label="Закрыть уведомления" onClick={() => setOpen(false)}><Icon name="close" size={20} /></button>
            </header>
            <div className="notification-drawer__toolbar">
              <span>{notifications.length} последних событий</span>
              <button type="button" disabled={unreadCount === 0} onClick={markAllRead}>Прочитать всё</button>
            </div>
            <div className="notification-list">
              {notifications.length === 0 ? (
                <div className="notification-empty"><Icon name="check" size={24} /><strong>Всё спокойно</strong><p>Новых решений и предупреждений нет.</p></div>
              ) : notifications.map((notification) => (
                <button
                  className={`notification-item notification-item--${notification.tone}${unreadSet.has(notification.id) ? " is-unread" : ""}`}
                  type="button"
                  onClick={() => openNotification(notification)}
                  key={notification.id}
                >
                  <span className="notification-item__icon"><Icon name={notification.tone === "action" ? "check" : "warning"} size={18} /></span>
                  <span className="notification-item__body">
                    <span className="notification-item__meta"><strong>{notification.source}</strong><time dateTime={notification.createdAt}>{shortDate(notification.createdAt)}</time></span>
                    <b>{notification.title}</b>
                    <span>{notification.message}</span>
                  </span>
                  {unreadSet.has(notification.id) ? <i aria-label="Не прочитано" /> : <Icon name="chevron" size={16} />}
                </button>
              ))}
            </div>
            <footer>
              <button type="button" onClick={() => { setOpen(false); onOpenApprovals(); }}>Все согласования<Icon name="chevron" size={16} /></button>
            </footer>
          </ModalLayer>
        </div>
      ), document.body) : null}
    </>
  );
}
