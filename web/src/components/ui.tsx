/** Мелкие переиспользуемые элементы интерфейса. */

import { useEffect, useRef, useState } from "react";

/**
 * Подсказка по значку «?»: появляется по наведению и по нажатию.
 *
 * Раньше пояснение к каждому блоку висело под заголовком постоянно.
 * На экране методиста таких пояснений одновременно было до девяти, и
 * страница читалась как сплошной текст, в котором надо искать элементы
 * управления. Объяснение нужно один раз — тому, кто видит блок впервые.
 *
 * Нажатие, а не только наведение: на сенсорном экране наведения нет,
 * и подсказка, доступная лишь мышью, там просто не существует.
 */
export function Hint({ text }: { text: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  // Координаты всплывашки считаются, а не задаются классом выравнивания.
  // Прижатие к правому краю значка уводило подсказку за левый край экрана
  // ровно так же, как левое прижатие — за правый: на узком экране 288 px
  // шире, чем расстояние до любого края. Позиция фиксированная и
  // ограничена окном с обеих сторон.
  const [box, setBox] = useState<{ left: number; top: number; width: number } | null>(
    null,
  );
  const anchor = useRef<HTMLSpanElement>(null);

  const show = () => {
    const a = anchor.current?.getBoundingClientRect();
    if (a) {
      const width = Math.min(288, window.innerWidth - 16);
      const left = Math.max(8, Math.min(a.left, window.innerWidth - width - 8));
      setBox({ left, top: a.bottom + 6, width });
    }
    setOpen(true);
  };

  return (
    <span ref={anchor} className="relative inline-flex align-middle">
      <button
        type="button"
        aria-label="Что это"
        className="flex h-4 w-4 items-center justify-center rounded-full border text-[10px] leading-none"
        style={{
          borderColor: open ? "var(--brand)" : "var(--border)",
          color: open ? "var(--brand)" : "var(--text-dim)",
        }}
        onMouseEnter={show}
        onMouseLeave={() => setOpen(false)}
        onFocus={show}
        onBlur={() => setOpen(false)}
        onClick={(e) => {
          e.stopPropagation();
          if (open) setOpen(false);
          else show();
        }}
      >
        ?
      </button>
      {open && box && (
        <span
          role="tooltip"
          className="fixed z-30 rounded border p-2 text-xs font-normal shadow-lg"
          style={{
            background: "var(--surface)",
            borderColor: "var(--border)",
            color: "var(--text)",
            left: box.left,
            top: box.top,
            width: box.width,
          }}
        >
          {text}
        </span>
      )}
    </span>
  );
}

/** Статус формальной проверки: четыре состояния, а не два. */
export function StatusIcon({ status }: { status: string }) {
  const map: Record<string, [string, string]> = {
    pass: ["✔", "var(--ok)"],
    fail: ["✘", "var(--err)"],
    unknown: ["?", "var(--warn)"],
    na: ["—", "var(--text-dim)"],
  };
  const [icon, color] = map[status] ?? ["?", "var(--text-dim)"];
  return (
    <span style={{ color }} className="font-bold" aria-hidden>
      {icon}
    </span>
  );
}

export function Badge({
  children,
  tone = "default",
  title,
}: {
  children: React.ReactNode;
  tone?: "default" | "ok" | "warn" | "err" | "info";
  title?: string;
}) {
  const colors: Record<string, string> = {
    default: "var(--text-dim)",
    ok: "var(--ok)",
    warn: "var(--warn)",
    err: "var(--err)",
    info: "var(--info)",
  };
  return (
    <span
      className="badge"
      title={title}
      style={{ color: colors[tone], borderColor: colors[tone] }}
    >
      {children}
    </span>
  );
}

/**
 * Состояние работы по-русски.
 *
 * Внутренние значения (`assigned`, `ai_reviewed`) в таблице методиста
 * показывались как есть: колонка «Статус» отвечала на английском словами
 * из базы данных. Перевод один на всё приложение, чтобы очередь ревьюера
 * и таблица методиста называли одно состояние одинаково.
 */
export function StatusBadge({ status }: { status: string }) {
  const map: Record<string, [string, "default" | "ok" | "warn" | "err" | "info"]> = {
    uploaded: ["загружена", "default"],
    assigned: ["ждёт проверки", "info"],
    in_review: ["идёт проверка", "info"],
    ai_reviewed: ["проверено автоматикой", "ok"],
    confirmed: ["оценка выставлена", "ok"],
    failed: ["проверить не удалось", "err"],
    returned: ["на доработке", "warn"],
  };
  const [text, tone] = map[status] ?? [status, "default"];
  return <Badge tone={tone}>{text}</Badge>;
}

/** Полоса заполнения — для баллов и индексов. */
export function Bar({
  value,
  max,
  tone,
}: {
  value: number;
  max: number;
  tone?: "ok" | "warn" | "err";
}) {
  const pct = max > 0 ? Math.min(100, (value / max) * 100) : 0;
  const color =
    tone === "err"
      ? "var(--err)"
      : tone === "warn"
        ? "var(--warn)"
        : tone === "ok"
          ? "var(--ok)"
          : pct >= 75
            ? "var(--ok)"
            : pct >= 40
              ? "var(--warn)"
              : "var(--err)";
  return (
    <div
      className="h-1.5 w-full overflow-hidden rounded"
      style={{ background: "var(--surface-2)" }}
    >
      <div className="h-full rounded" style={{ width: `${pct}%`, background: color }} />
    </div>
  );
}

/** Живой обратный отсчёт до момента времени. Тикает раз в секунду. */
export function Countdown({ to, prefix = "" }: { to: string | null; prefix?: string }) {
  const [, force] = useState(0);
  useEffect(() => {
    const t = setInterval(() => force((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, []);

  if (!to) return <span className="muted">не задан</span>;
  const left = (new Date(to).getTime() - Date.now()) / 1000;
  const past = left < 0;
  const s = Math.abs(Math.floor(left));
  const parts =
    s < 60
      ? `${s} с`
      : s < 3600
        ? `${Math.floor(s / 60)} мин ${s % 60} с`
        : s < 86400
          ? `${Math.floor(s / 3600)} ч ${Math.floor((s % 3600) / 60)} мин`
          : `${Math.floor(s / 86400)} дн ${Math.floor((s % 86400) / 3600)} ч`;

  return (
    <span style={{ color: past ? "var(--err)" : left < 60 ? "var(--warn)" : "inherit" }}>
      {prefix}
      {past ? "прошло " : ""}
      {parts}
      {past ? " назад" : ""}
    </span>
  );
}

/** Часы сервера с секундами. Время настоящее — никакого демо-времени. */
export function Clock() {
  const [now, setNow] = useState(new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);
  return (
    <span className="tabular-nums muted" title="Текущее время сервера">
      {now.toLocaleTimeString("ru-RU")}
    </span>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-2 muted">
      <span
        className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-t-transparent"
        style={{ borderColor: "var(--brand)", borderTopColor: "transparent" }}
      />
      {label}
    </span>
  );
}

/**
 * Кнопка действия с явными состояниями: ждём → сделано.
 *
 * Обычная кнопка после нажатия выглядела ровно так же, как до нажатия:
 * «Подтвердить и отправить» не менялась ни цветом, ни текстом, и понять,
 * ушёл результат или нет, было невозможно. Всплывающее сообщение уезжало
 * в угол экрана и через шесть секунд исчезало, а кнопка так и оставалась
 * приглашением нажать ещё раз.
 *
 * Поэтому состояние живёт в самой кнопке: на время запроса она блокируется
 * и показывает вращающийся индикатор, после успеха — галочку и текст
 * `doneLabel` на три секунды. Повторное нажатие во время запроса
 * невозможно, двойных отправок не бывает.
 */
export function ActionButton({
  onAction,
  children,
  doneLabel = "Готово",
  primary = false,
  disabled = false,
  title,
  className = "",
  confirm,
}: {
  onAction: () => Promise<unknown>;
  children: React.ReactNode;
  doneLabel?: string;
  primary?: boolean;
  disabled?: boolean;
  title?: string;
  className?: string;
  /** Текст вопроса перед необратимым действием. */
  confirm?: string;
}) {
  const [state, setState] = useState<"idle" | "busy" | "done">("idle");

  useEffect(() => {
    if (state !== "done") return;
    const t = setTimeout(() => setState("idle"), 3000);
    return () => clearTimeout(t);
  }, [state]);

  const click = () => {
    if (state === "busy") return;
    if (confirm && !window.confirm(confirm)) return;
    setState("busy");
    onAction()
      .then(() => setState("done"))
      .catch(() => setState("idle"));
  };

  return (
    <button
      className={`btn ${primary ? "btn-primary" : ""} ${className}`}
      onClick={click}
      disabled={disabled || state === "busy"}
      title={title}
      style={
        state === "done"
          ? { borderColor: "var(--ok)", color: primary ? undefined : "var(--ok)" }
          : undefined
      }
    >
      {state === "busy" && (
        <span
          className="mr-1.5 inline-block h-3 w-3 animate-spin rounded-full border-2 border-t-transparent align-[-1px]"
          style={{ borderColor: "currentColor", borderTopColor: "transparent" }}
        />
      )}
      {state === "done" ? `✓ ${doneLabel}` : children}
    </button>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div className="py-10 text-center muted" style={{ fontSize: 13 }}>
      {children}
    </div>
  );
}

/** Секция со сворачиванием — карточка ревью длинная. */
export function Section({
  title,
  hint,
  right,
  children,
  defaultOpen = true,
}: {
  title: React.ReactNode;
  /** Одна строка: что это за блок и зачем он тут. */
  hint?: React.ReactNode;
  right?: React.ReactNode;
  children: React.ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="card">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <button
            className="flex items-center gap-2 text-left font-semibold"
            onClick={() => setOpen((v) => !v)}
          >
            <span className="muted text-xs">{open ? "▾" : "▸"}</span>
            {title}
          </button>
          {/*
            Пояснение живёт в значке «?» рядом с заголовком, а не строкой
            под ним. Постоянный текст под каждым из девяти блоков превращал
            рабочий экран в инструкцию: элементы управления приходилось
            выискивать между абзацами.
          */}
          {hint && <Hint text={hint} />}
        </div>
        {right}
      </div>
      {/*
        Горизонтальный скролл живёт внутри секции, а не на странице.
        Без этого широкие таблицы (рубрика, очередь, разбор по прогонам)
        растягивали весь документ: на экране 375 px страница уезжала вбок
        на 557 px, и шапка с содержимым разъезжались при прокрутке.
        Решение общее для всех секций — точечная обёртка каждой таблицы
        неизбежно пропустила бы следующую добавленную.
      */}
      {open && <div className="mt-3 overflow-x-auto">{children}</div>}
    </div>
  );
}

export function Toast({
  message,
  tone = "info",
  count = 1,
  onClose,
}: {
  message: string;
  tone?: "info" | "ok" | "warn" | "err";
  /** Сколько раз пришло одно и то же сообщение. */
  count?: number;
  onClose: () => void;
}) {
  // Отсчёт перезапускается при каждом повторе: пока сообщение продолжает
  // приходить, карточка висит, а как только поток прекратился — уходит.
  useEffect(() => {
    const t = setTimeout(onClose, tone === "err" ? 12000 : 6000);
    return () => clearTimeout(t);
  }, [onClose, count, tone]);

  const colors: Record<string, string> = {
    info: "var(--info)",
    ok: "var(--ok)",
    warn: "var(--warn)",
    err: "var(--err)",
  };
  return (
    <div
      className="card flex max-w-md items-start gap-3 shadow-lg"
      style={{ borderLeft: `3px solid ${colors[tone]}` }}
      role="status"
    >
      <div className="flex-1 text-sm">
        {message}
        {count > 1 && (
          <span className="muted ml-1.5 tabular-nums text-xs">×{count}</span>
        )}
      </div>
      <button className="muted text-xs" onClick={onClose} aria-label="Закрыть">
        ✕
      </button>
    </div>
  );
}
