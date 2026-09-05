/**
 * Оболочка приложения: вход, три роли, живая шина событий, тема.
 *
 * Роль определяет вкладку целиком. Данные фильтруются на сервере, а не
 * здесь: студент не должен получать чужие внутренние флаги даже в теле
 * ответа, поэтому интерфейс не «прячет» их, а просто не получает.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { api, getUser, setUser, subscribe, type User } from "./api";
import { Clock, Spinner, Toast } from "./components/ui";
import Coordinator from "./pages/Coordinator";
import Quality from "./pages/Quality";
import Login from "./pages/Login";
import Reviewer from "./pages/Reviewer";
import Student from "./pages/Student";

export interface ToastMsg {
  id: number;
  text: string;
  tone: "info" | "ok" | "warn" | "err";
  /** Сколько раз пришло одно и то же сообщение. */
  count: number;
}

const THEME_KEY = "avito_reviewer_theme";

/**
 * Сколько сообщений держим на экране одновременно.
 *
 * Без предела лента забивалась: одно движение ползунка веса давало запрос,
 * запрос давал сообщение «Пересчитано ревью: 3», и девять таких карточек
 * закрывали правую половину экрана до самого верха. Предел плюс склейка
 * повторов оставляют на экране только свежее.
 */
const TOAST_LIMIT = 4;

export default function App() {
  const [user, setU] = useState<User | null>(getUser());
  const [health, setHealth] = useState<any>(null);
  const [notifications, setNotifications] = useState<any[]>([]);
  const [toasts, setToasts] = useState<ToastMsg[]>([]);
  const [tick, setTick] = useState(0);
  // Вторая вкладка внутри роли — качество работы самой системы.
  // «Приватность» вкладкой была, но это отчёт о контуре, а не рабочий
  // экран: ревьюеру он не нужен ни разу за проверку. Переехал к методисту
  // в «Настройки», рядом с остальным управлением стендом.
  const [view, setView] = useState<"main" | "quality">("main");
  const [dark, setDark] = useState<boolean>(() => {
    const saved = localStorage.getItem(THEME_KEY);
    if (saved) return saved === "dark";
    return window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
  });

  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
    localStorage.setItem(THEME_KEY, dark ? "dark" : "light");
  }, [dark]);

  const toast = useCallback((text: string, tone: ToastMsg["tone"] = "info") => {
    setToasts((t) => {
      // Повтор того же текста не плодит карточку, а увеличивает счётчик:
      // «Пересчитано ревью: 3 ×9» вместо девяти одинаковых карточек.
      const last = t[t.length - 1];
      if (last && last.text === text && last.tone === tone) {
        return [...t.slice(0, -1), { ...last, count: last.count + 1 }];
      }
      return [...t, { id: Date.now() + Math.random(), text, tone, count: 1 }].slice(
        -TOAST_LIMIT,
      );
    });
  }, []);

  /** Сигнал «данные изменились» — страницы перезагружают то, что показывают. */
  const refresh = useCallback(() => setTick((n) => n + 1), []);

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth({ ok: false }));
  }, [tick]);

  const loadNotifications = useCallback(() => {
    if (!user) return;
    api.notifications().then(setNotifications).catch(() => {});
  }, [user]);

  useEffect(loadNotifications, [loadNotifications]);

  // Живая шина: одна подписка на всё приложение.
  useEffect(() => {
    if (!user) return;
    return subscribe(user, {
      notification: (d) => {
        toast(d.title + (d.body ? ` — ${d.body}` : ""), d.severity ?? "info");
        loadNotifications();
      },
      review_ready: (d) => {
        toast(
          `Проверка выполнена: ${d.score} из ${d.max_score}` +
            (d.failed_steps?.length ? ` (отказали шаги: ${d.failed_steps.join(", ")})` : ""),
          d.failed_steps?.length ? "warn" : "ok",
        );
        refresh();
      },
      review_confirmed: () => refresh(),
      deadline_changed: (d) => {
        toast(`Срок: ${d.label}. ${d.detail ?? ""}`, d.state === "late_zero" ? "err" : "warn");
        refresh();
      },
      allocation_done: (d) => {
        toast(`Распределено работ: ${d.assigned}`, "ok");
        refresh();
      },
      submission_created: () => refresh(),
      rubric_updated: () => refresh(),
      deadlines_updated: () => refresh(),
      job_queued: () => refresh(),
      job_failed: (d) => toast(`Проверить не удалось: ${d.error}`, "err"),
      demo_reset: () => {
        toast("Демо-данные сброшены", "info");
        refresh();
      },
    });
  }, [user, toast, refresh, loadNotifications]);

  const unread = useMemo(() => notifications.filter((n) => !n.read).length, [notifications]);

  const logout = () => {
    setUser(null);
    setU(null);
  };

  if (!user) {
    return (
      <Login
        onLogin={(u) => {
          setUser(u);
          setU(u);
        }}
      />
    );
  }

  const roleTitle = {
    coordinator: "Методист",
    reviewer: "Ревьюер",
    student: "Студент",
  }[user.role];

  return (
    <div className="min-h-full">
      <header
        className="sticky top-0 z-20 border-b px-4 py-2"
        style={{ background: "var(--surface)", borderColor: "var(--border)" }}
      >
        <div className="mx-auto flex max-w-[1500px] flex-wrap items-center gap-3">
          <div className="font-semibold">
            Avito <span style={{ color: "var(--brand)" }}>AI Reviewer</span>
          </div>
          {/*
            Переключатель ролей прямо в шапке. Экран входа существовал и
            раньше, но при уже сохранённой сессии вкладки пользователь его
            больше никогда не видел: приложение сразу открывало роль, и
            единственным способом сменить её была кнопка «Выйти» в дальнем
            правом углу. Со стороны это выглядело так, будто входа нет вовсе.
          */}
          <UserSwitcher
            user={user}
            roleTitle={roleTitle}
            onSwitch={(u) => {
              setUser(u);
              setU(u);
              setView("main");
              refresh();
            }}
          />

          {user.role !== "student" && (
            <nav className="flex gap-1">
              {(
                [
                  ["main", roleTitle],
                  ["quality", "Качество"],
                ] as const
              ).map(([key, label]) => (
                <button
                  key={key}
                  className="btn text-xs"
                  onClick={() => setView(key)}
                  style={
                    view === key
                      ? { borderColor: "var(--brand)", color: "var(--brand)" }
                      : undefined
                  }
                >
                  {label}
                </button>
              ))}
            </nav>
          )}

          {/*
            Правая группа переносится по словам, а не тянет шапку.
            Раньше `flex` без `flex-wrap` держал часы, два бейджа и три
            кнопки в одну строку шириной 409 px — на телефоне кнопка
            «Выйти» уезжала за край экрана.
          */}
          <div className="ml-auto flex flex-wrap items-center justify-end gap-2 text-xs sm:gap-3">
            {/* Часы с секундами нужны у панели сроков; в узкой шапке они
                занимают место, которое дороже отдать кнопкам. */}
            <span className="hidden sm:inline">
              <Clock />
            </span>
            <OfflineBadge health={health} />
            <NotificationBell
              count={unread}
              items={notifications}
              onOpen={() => api.markRead().then(loadNotifications)}
            />
            <button
              className="btn"
              onClick={() => setDark((v) => !v)}
              title="Переключить тему"
            >
              {dark ? "☀" : "☾"}
            </button>
            <button className="btn" onClick={logout} title="Вернуться на экран входа">
              Выйти
            </button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1500px] p-4">
        {view === "quality" && user.role !== "student" ? (
          <Quality />
        ) : (
          <>
            {user.role === "coordinator" && (
              <Coordinator user={user} tick={tick} toast={toast} refresh={refresh} />
            )}
            {user.role === "reviewer" && (
              <Reviewer user={user} tick={tick} toast={toast} refresh={refresh} />
            )}
            {user.role === "student" && (
              <Student user={user} tick={tick} toast={toast} refresh={refresh} />
            )}
          </>
        )}
      </main>

      <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2">
        {toasts.map((t) => (
          <Toast
            key={t.id}
            message={t.text}
            count={t.count}
            tone={t.tone}
            onClose={() => setToasts((all) => all.filter((x) => x.id !== t.id))}
          />
        ))}
        {toasts.length > 1 && (
          <button
            className="btn self-end text-xs"
            onClick={() => setToasts([])}
          >
            Скрыть все
          </button>
        )}
      </div>
    </div>
  );
}

/**
 * Баннер офлайн-контура.
 *
 * Показывает фактическое число внешних вызовов из аудита, а не декларацию.
 * Это ответ на требование кейса описать меры защиты данных: число берётся
 * из перехваченных вызовов.
 */
function OfflineBadge({ health }: { health: any }) {
  if (!health) return null;
  const a = health.audit ?? {};
  const external = a.external_calls ?? 0;
  const llmOk = health.llm?.ok && health.llm?.model_available;

  return (
    <div className="flex items-center gap-2">
      <span
        className="badge"
        title={`Разрешённые хосты: ${(a.allowed_hosts ?? []).join(", ")}`}
        style={{
          color: external === 0 ? "var(--ok)" : "var(--warn)",
          borderColor: external === 0 ? "var(--ok)" : "var(--warn)",
        }}
      >
        внешних вызовов: {external}
      </span>
      <span
        className="badge"
        title={
          llmOk
            ? `${health.llm.model} — ${health.llm.base_url}`
            : (health.llm?.error ?? "модель недоступна")
        }
        style={{
          color: llmOk ? "var(--ok)" : "var(--err)",
          borderColor: llmOk ? "var(--ok)" : "var(--err)",
        }}
      >
        {llmOk ? "модель локально" : "модель недоступна"}
      </span>
    </div>
  );
}

/**
 * Смена роли без возврата на экран входа.
 *
 * Демонстрация идёт тремя ролями одновременно, и переключаться приходится
 * постоянно. Сессия лежит в `sessionStorage`, то есть у каждой вкладки
 * своя — здесь об этом прямо сказано, чтобы способ «три вкладки» не
 * приходилось угадывать.
 */
function UserSwitcher({
  user,
  roleTitle,
  onSwitch,
}: {
  user: User;
  roleTitle: string;
  onSwitch: (u: User) => void;
}) {
  const [open, setOpen] = useState(false);
  const [users, setUsers] = useState<User[] | null>(null);

  useEffect(() => {
    if (open && !users) api.users().then(setUsers).catch(() => setUsers([]));
  }, [open, users]);

  const groups: [User["role"], string][] = [
    ["coordinator", "Методисты"],
    ["reviewer", "Ревьюеры"],
    ["student", "Студенты"],
  ];

  return (
    <div className="relative">
      <button
        className="flex items-center gap-2 rounded border px-2 py-1"
        style={{ borderColor: "var(--border)" }}
        onClick={() => setOpen((v) => !v)}
        title="Сменить роль или пользователя"
      >
        <span className="badge">{roleTitle}</span>
        <span className="text-sm">{user.name}</span>
        <span className="muted text-xs">▾</span>
      </button>

      {open && (
        <>
          {/* Клик мимо закрывает список — иначе он висит поверх работы. */}
          <div className="fixed inset-0 z-30" onClick={() => setOpen(false)} />
          <div
            className="absolute left-0 z-40 mt-1 max-h-[70vh] w-72 overflow-auto rounded-lg border p-2 shadow-xl"
            style={{ background: "var(--surface)", borderColor: "var(--border)" }}
          >
            <div className="muted px-1 pb-1 text-xs">
              Войти другой ролью. Сессия у каждой вкладки своя — откройте
              вторую вкладку, чтобы держать две роли сразу.
            </div>
            {!users && <div className="p-2"><Spinner label="Загрузка…" /></div>}
            {groups.map(([role, title]) => {
              const list = (users ?? []).filter((u) => u.role === role);
              if (!list.length) return null;
              return (
                <div key={role} className="mt-1">
                  <div className="muted px-1 text-[11px] uppercase">{title}</div>
                  {list.map((u) => (
                    <button
                      key={u.id}
                      className="flex w-full items-center gap-2 rounded px-1.5 py-1 text-left text-sm"
                      style={
                        u.id === user.id
                          ? { background: "var(--surface-2)", color: "var(--brand)" }
                          : undefined
                      }
                      onClick={() => {
                        setOpen(false);
                        if (u.id !== user.id) onSwitch(u);
                      }}
                    >
                      {u.id === user.id ? "●" : "○"} {u.name}
                    </button>
                  ))}
                </div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}

function NotificationBell({
  count,
  items,
  onOpen,
}: {
  count: number;
  items: any[];
  onOpen: () => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="relative">
      <button
        className="btn"
        onClick={() => {
          setOpen((v) => !v);
          if (!open) onOpen();
        }}
      >
        🔔
        {count > 0 && (
          <span
            className="ml-1 rounded-full px-1.5 text-[10px] font-bold text-white"
            style={{ background: "var(--err)" }}
          >
            {count}
          </span>
        )}
      </button>
      {open && (
        <div
          className="absolute right-0 mt-1 max-h-[70vh] w-96 overflow-auto rounded-lg border p-2 shadow-xl"
          style={{ background: "var(--surface)", borderColor: "var(--border)" }}
        >
          {items.length === 0 && <div className="p-4 text-center muted">Уведомлений нет</div>}
          {items.map((n) => (
            <div
              key={n.id}
              className="border-b p-2 last:border-0"
              style={{ borderColor: "var(--border)" }}
            >
              <div className="flex items-baseline justify-between gap-2">
                <span
                  className="text-sm font-medium"
                  style={{
                    color:
                      n.severity === "error"
                        ? "var(--err)"
                        : n.severity === "warning"
                          ? "var(--warn)"
                          : n.severity === "success"
                            ? "var(--ok)"
                            : "inherit",
                  }}
                >
                  {n.title}
                </span>
                <span className="muted text-[11px] whitespace-nowrap">
                  {new Date(n.created_at).toLocaleTimeString("ru-RU")}
                </span>
              </div>
              {n.body && <div className="muted mt-0.5 text-xs">{n.body}</div>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
