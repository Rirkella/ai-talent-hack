/**
 * Вкладка студента: свои работы, сроки и обратная связь.
 *
 * Показывается только то, что подтвердил человек. Предварительный балл,
 * индекс приоритета, сигнал генеративного ИИ и схожесть с чужими работами
 * сюда не приходят вовсе — сервер их не отдаёт этой роли.
 */

import { useCallback, useEffect, useState } from "react";

import { api, download, type User } from "../api";
import { ActionButton, Badge, Empty, Section, Spinner } from "../components/ui";
import StudentProfile from "./StudentProfile";

type Toast = (t: string, tone?: "info" | "ok" | "warn" | "err") => void;

export default function Student({
  user,
  tick,
  toast,
  refresh,
}: {
  user: User;
  tick: number;
  toast: Toast;
  refresh: () => void;
}) {
  const [items, setItems] = useState<any[] | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [detail, setDetail] = useState<any>(null);
  const [localTick, setLocalTick] = useState(0);

  const bump = useCallback(() => {
    setLocalTick((n) => n + 1);
    refresh();
  }, [refresh]);

  useEffect(() => {
    api.submissions().then(setItems);
  }, [tick, localTick, user.id]);

  useEffect(() => {
    if (open) api.submission(open).then(setDetail);
    else setDetail(null);
  }, [open, tick, localTick]);

  if (!items) return <Spinner label="Загрузка…" />;

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-3">
      <SubmitWork user={user} items={items} toast={toast} onSent={bump} />

      <h2 className="font-semibold">Мои работы</h2>
      {items.length === 0 && (
        <Empty>Вы ещё ничего не сдавали. Отправьте работу формой выше.</Empty>
      )}

      {items.map((s) => (
        <div key={s.id} className="card">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="font-medium">{s.assignment_title || s.file_name}</div>
              <div className="muted text-xs">
                {s.file_name} · отправлено{" "}
                {new Date(s.submitted_at).toLocaleString("ru-RU")}
              </div>
            </div>
            <div className="text-right">
              {s.final_score != null ? (
                <>
                  <div className="muted text-xs">оценка</div>
                  <div className="text-2xl font-semibold tabular-nums">
                    {s.final_score}
                    <span className="muted text-base"> / {s.max_score ?? "—"}</span>
                  </div>
                </>
              ) : (
                <Badge tone="info">на проверке</Badge>
              )}
            </div>
          </div>

          <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
            <Badge
              tone={
                s.deadline_state === "late_zero"
                  ? "err"
                  : s.deadline_state === "late_penalty"
                    ? "warn"
                    : "ok"
              }
            >
              {s.deadline_label}
            </Badge>
            {s.late_penalty > 0 && <Badge tone="err">штраф −{s.late_penalty} балл</Badge>}
            <span className="muted">{s.deadline_detail}</span>
          </div>

          {s.review_state_text && (
            <div className="muted mt-1 text-xs">Проверка: {s.review_state_text}</div>
          )}

          <div className="mt-3 flex gap-2">
            <button className="btn" onClick={() => setOpen(open === s.id ? null : s.id)}>
              {open === s.id ? "Свернуть" : "Обратная связь"}
            </button>
            {s.final_score != null && (
              <button
                className="btn"
                onClick={() =>
                  download(api.exportReviewUrl(s.id, "md"), `feedback_${s.id.slice(0, 8)}.md`)
                }
              >
                Скачать
              </button>
            )}
          </div>

          {open === s.id && (
            <div className="mt-3 rounded p-3" style={{ background: "var(--surface-2)" }}>
              {!detail && <Spinner label="Загрузка…" />}
              {detail && !detail.published && (
                <div className="muted text-sm">
                  Проверка ещё не завершена. Обратная связь появится здесь после того,
                  как ревьюер подтвердит результат.
                </div>
              )}
              {detail?.published && (
                <div className="whitespace-pre-wrap text-sm">{detail.feedback}</div>
              )}
            </div>
          )}
        </div>
      ))}

      <div className="card">
        <div className="mb-1 text-sm font-medium">Сроки проверки</div>
        {items.map((s) => (
          <div key={s.id} className="flex justify-between gap-3 py-0.5 text-xs">
            <span className="muted truncate">{s.assignment_title || s.file_name}</span>
            <span className="whitespace-nowrap">
              {s.confirmed_at
                ? `проверено ${new Date(s.confirmed_at).toLocaleString("ru-RU")}`
                : s.review_state_text || "ожидает проверки"}
            </span>
          </div>
        ))}
        <p className="muted mt-2 text-xs">
          Уведомления о приближении дедлайна приходят в колокольчик наверху.
        </p>
      </div>

      {/*
        Профиль показывает динамику и сильные/слабые стороны по критериям.
        Строится только по подтверждённым работам: до решения человека
        никакой картинки «вот твои слабые стороны» студент не увидит.
      */}
      <StudentProfile studentId={user.id} tick={tick} title="Мой прогресс" />
    </div>
  );
}

/**
 * Сдача работы студентом.
 *
 * Раньше загрузить работу мог только методист, а студент видел лишь то,
 * что за него загрузили. Это ломало сам сценарий: сдаёт работу студент.
 *
 * Повторная сдача не создаёт вторую строку в очереди: если по заданию уже
 * есть работа, новая уходит как следующая версия той же — ревьюер видит
 * сравнение с предыдущей, а не две несвязанные работы.
 */
function SubmitWork({
  user,
  items,
  toast,
  onSent,
}: {
  user: User;
  items: any[];
  toast: Toast;
  onSent: () => void;
}) {
  const [assignments, setAssignments] = useState<any[] | null>(null);
  const [chosen, setChosen] = useState<string>("");
  const [file, setFile] = useState<File | null>(null);
  // Свободная тема: работа не по карточке из списка, а со своим названием.
  // Нужна, когда преподаватель дал задание на словах и карточки ещё нет.
  const [ownTitle, setOwnTitle] = useState("");
  const [ownTrack, setOwnTrack] = useState("product_fraud");
  const [tracks, setTracks] = useState<{ id: string; name: string }[]>([]);

  useEffect(() => {
    api.tracks().then((r) => setTracks(r.tracks ?? [])).catch(() => setTracks([]));
  }, []);

  useEffect(() => {
    api
      .assignments()
      .then((rows) => {
        setAssignments(rows);
        setChosen((c) => c || rows[0]?.id || "");
      })
      .catch(() => setAssignments([]));
  }, []);

  const free = chosen === "__free__";
  const assignment = (assignments ?? []).find((a) => a.id === chosen);
  // Предыдущая версия по этому же заданию: сдача становится пересдачей.
  // У свободной темы предыдущей версии нет — это всегда новая работа.
  const previous = free
    ? undefined
    : items.find((s) => s.assignment_id === chosen && !s.superseded);

  const ready = file && (free ? ownTitle.trim().length >= 3 : Boolean(assignment));

  const send = () => {
    if (!file) return Promise.reject(new Error("нет файла"));
    const fd = new FormData();
    if (free) {
      fd.append("new_assignment_title", ownTitle.trim());
      fd.append("track", ownTrack);
    } else {
      if (!assignment) return Promise.reject(new Error("не выбрано задание"));
      fd.append("assignment_id", assignment.id);
      fd.append("track", assignment.track);
    }
    fd.append("student_id", user.id);
    fd.append("file", file);
    if (previous) fd.append("replaces", previous.id);
    return api
      .uploadSubmission(fd)
      .then((r) => {
        toast(
          previous
            ? `Отправлено как версия ${r.version ?? 2}. Ревьюер увидит, что изменилось.`
            : "Работа отправлена на проверку.",
          "ok",
        );
        (r.warnings ?? []).forEach((w: string) => toast(w, "warn"));
        setFile(null);
        onSent();
      })
      .catch((e) => {
        toast(String(e.message ?? e), "err");
        throw e;
      });
  };

  return (
    <Section
      title="Сдать работу"
      hint="Выберите задание и приложите файл. Повторная сдача заменяет предыдущую версию, а не создаёт вторую работу."
    >
      {!assignments && <Spinner label="Загрузка заданий…" />}
      {assignments?.length === 0 && (
        <Empty>Заданий пока нет — методист их ещё не создал.</Empty>
      )}
      {assignments && assignments.length > 0 && (
        <div className="flex flex-col gap-3">
          <label className="block">
            <span className="muted mb-1 block text-xs">Задание</span>
            <select
              className="input"
              value={chosen}
              onChange={(e) => setChosen(e.target.value)}
            >
              {assignments.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.title} · {a.course || a.track}
                </option>
              ))}
              <option value="__free__">— своя тема: указать название самому —</option>
            </select>
          </label>

          {free && (
            <div className="rounded p-3" style={{ background: "var(--surface-2)" }}>
              <label className="mb-2 block">
                <span className="muted mb-1 block text-xs">Название работы</span>
                <input
                  className="input"
                  placeholder="Например: ДЗ №3. Анализ конкурентов"
                  value={ownTitle}
                  onChange={(e) => setOwnTitle(e.target.value)}
                />
              </label>
              <label className="block">
                <span className="muted mb-1 block text-xs">Направление курса</span>
                <select
                  className="input"
                  value={ownTrack}
                  onChange={(e) => setOwnTrack(e.target.value)}
                >
                  {tracks.map((tr) => (
                    <option key={tr.id} value={tr.id}>
                      {tr.name}
                    </option>
                  ))}
                </select>
              </label>
              <p className="muted mt-2 text-xs">
                Работа со своей темой попадёт к ревьюеру, но автоматическая
                проверка по ней не запустится, пока ревьюер или методист не
                опишет критерии — за что ставить баллы, кроме вас двоих,
                никто не знает.
              </p>
            </div>
          )}

          <label className="block">
            <span className="muted mb-1 block text-xs">
              Файл работы — docx, pdf, xlsx, md, ipynb, zip с репозиторием и другие
            </span>
            <input
              type="file"
              className="input"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
          </label>

          {previous && (
            <div
              className="rounded p-2 text-xs"
              style={{ background: "var(--surface-2)", color: "var(--warn)" }}
            >
              По этому заданию вы уже сдавали «{previous.file_name}». Новый файл
              уйдёт как следующая версия: предыдущая сохранится, ревьюер увидит
              сравнение.
            </div>
          )}

          <div className="flex items-center gap-3">
            <ActionButton
              primary
              disabled={!ready}
              doneLabel="Отправлено"
              onAction={send}
            >
              {previous ? "Отправить новую версию" : "Отправить на проверку"}
            </ActionButton>
            <span className="muted text-xs">
              {file ? file.name : "файл не выбран"}
            </span>
          </div>

          <p className="muted text-xs">
            После отправки работа попадает в очередь проверки. Оценку и
            комментарии вы увидите здесь же, когда ревьюер подтвердит результат:
            до этого момента ничего не публикуется.
          </p>
        </div>
      )}
    </Section>
  );
}
