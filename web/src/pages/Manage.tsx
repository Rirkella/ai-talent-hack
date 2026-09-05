/**
 * Управление потоком: создание задания, загрузка работ, состав участников.
 *
 * Без этих форм «добавление курса через интерфейс» было бы неправдой:
 * API существовал, но нажать было нечего, и реальную домашку загрузить
 * можно было только curl'ом. Этот экран закрывает путь целиком —
 * от создания задания до появления работы в очереди ревьюера.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { api, type User } from "../api";
import { Badge, Empty, Section, Spinner } from "../components/ui";
import { trackName } from "./Coordinator";

type Toast = (t: string, tone?: "info" | "ok" | "warn" | "err") => void;

interface Refs {
  tracks: { id: string; name: string }[];
  channels: { id: string; name: string }[];
  formats: string[];
}

/** Создание задания — первый шаг добавления нового курса или ДЗ. */
export function CreateAssignment({
  refs,
  toast,
  onCreated,
}: {
  refs: Refs | null;
  toast: Toast;
  onCreated: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [track, setTrack] = useState("product_fraud");
  const [homeworkNo, setHomeworkNo] = useState(1);
  const [channel, setChannel] = useState("stepik");
  const [busy, setBusy] = useState(false);

  if (!open) {
    return (
      <button className="btn" onClick={() => setOpen(true)}>
        + Новое задание
      </button>
    );
  }

  const submit = () => {
    if (!title.trim()) {
      toast("Укажите название задания", "warn");
      return;
    }
    setBusy(true);
    api
      .createAssignment({ title, track, homework_no: homeworkNo, channel })
      .then((a) => {
        toast("Задание создано. Дальше — критерии оценивания: загрузите условие или напишите требования текстом.", "ok");
        setOpen(false);
        setTitle("");
        onCreated(a.id);
      })
      .catch((e) => toast(String(e.message ?? e), "err"))
      .finally(() => setBusy(false));
  };

  return (
    <div className="card">
      <div className="mb-2 font-semibold">Новое задание</div>
      <p className="muted mb-3 text-xs">
        После создания задайте критерии: загрузите файл условия или напишите
        требования текстом. Баллы, требования к оформлению и правило штрафа
        извлекутся автоматически. Править код не нужно.
      </p>
      <div className="grid gap-2 sm:grid-cols-2">
        <label className="text-xs">
          <span className="muted">Название</span>
          <input
            className="input mt-0.5"
            value={title}
            placeholder="ДЗ №1. Карта рисков продукта"
            onChange={(e) => setTitle(e.target.value)}
          />
        </label>
        <label className="text-xs">
          <span className="muted">Направление</span>
          <select
            className="input mt-0.5"
            value={track}
            onChange={(e) => setTrack(e.target.value)}
          >
            {(refs?.tracks ?? []).map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        </label>
        <label className="text-xs">
          <span className="muted">Номер ДЗ</span>
          <input
            type="number"
            min={1}
            className="input mt-0.5"
            value={homeworkNo}
            onChange={(e) => setHomeworkNo(Number(e.target.value) || 1)}
          />
        </label>
        <label className="text-xs">
          <span className="muted">Канал сдачи</span>
          <select
            className="input mt-0.5"
            value={channel}
            onChange={(e) => setChannel(e.target.value)}
          >
            {(refs?.channels ?? []).map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="mt-3 flex gap-2">
        <button className="btn btn-primary" disabled={busy} onClick={submit}>
          {busy ? "Создание…" : "Создать"}
        </button>
        <button className="btn" onClick={() => setOpen(false)}>
          Отмена
        </button>
      </div>
    </div>
  );
}

/**
 * Загрузка работ студентов.
 *
 * Тег направления обязателен: система дополнительно определяет его сама и
 * предупреждает при расхождении, но решает человек. Молча положить работу
 * другого курса в этот поток нельзя.
 */
export function UploadSubmissions({
  assignment,
  students,
  refs,
  toast,
  onChanged,
}: {
  assignment: any;
  students: User[];
  refs: Refs | null;
  toast: Toast;
  onChanged: () => void;
}) {
  const [studentId, setStudentId] = useState("");
  const [busy, setBusy] = useState(false);
  const [warnings, setWarnings] = useState<string[]>([]);
  // Повторная сдача: новая версия существующей работы, а не отдельная строка.
  const [replaces, setReplaces] = useState("");
  const [existing, setExisting] = useState<any[]>([]);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!studentId && students.length) setStudentId(students[0].id);
  }, [students, studentId]);

  // Список предыдущих работ выбранного студента — из них выбирается та,
  // которую заменяет новая версия.
  useEffect(() => {
    setReplaces("");
    if (!studentId) return;
    api
      .submissions(assignment.id)
      .then((all) =>
        setExisting(
          all.filter(
            (x: any) =>
              x.student_id === studentId &&
              !x.superseded &&
              // Строки без файла — занятое место в очереди прошлого потока.
              // Заменять «версию 1» того, чего никто не сдавал, бессмысленно,
              // а в списке они выглядели как настоящие работы студента.
              x.has_file !== false,
          ),
        ),
      )
      .catch(() => setExisting([]));
  }, [studentId, assignment.id, onChanged]);

  const upload = async (files: FileList) => {
    if (!studentId) {
      toast("Выберите студента", "warn");
      return;
    }
    setBusy(true);
    setWarnings([]);
    const collected: string[] = [];
    let ok = 0;

    // Файлы отправляются по одному: у каждого свой ответ с предупреждениями,
    // и падение одного не должно отменять остальные.
    for (const file of Array.from(files)) {
      const fd = new FormData();
      fd.append("assignment_id", assignment.id);
      fd.append("student_id", studentId);
      fd.append("track", assignment.track);
      fd.append("file", file);
      // Заменяемая работа указывается только для первого файла: «версия 2»
      // — это один документ, а не пачка. Остальные файлы пойдут как новые.
      if (replaces && ok === 0) fd.append("replaces", replaces);
      try {
        const r = await api.uploadSubmission(fd);
        ok += 1;
        for (const w of r.warnings ?? []) collected.push(`${file.name}: ${w}`);
      } catch (e: any) {
        collected.push(`${file.name}: ${e.message ?? e}`);
      }
    }

    setWarnings(collected);
    toast(
      `Загружено работ: ${ok} из ${files.length}` +
        (collected.length ? `, предупреждений: ${collected.length}` : ""),
      collected.length ? "warn" : "ok",
    );
    setBusy(false);
    setReplaces("");
    if (fileRef.current) fileRef.current.value = "";
    onChanged();
  };

  return (
    <Section
      title="Загрузить файл работы за студента"
      hint={
        <>
          Обычно работу сдаёт студент сам со своей вкладки. Этот блок — для
          случая, когда файл пришёл мимо системы: почтой, в чат или выгрузкой
          из Stepik. Вы указываете, чей это файл, и прикладываете сам файл;
          работа встаёт в очередь на проверку. Принимаются{" "}
          {(refs?.formats ?? []).length} расширений — документы, таблицы,
          ноутбуки, исходный код и <code>.zip</code> с репозиторием.
          Направление берётся из задания («{trackName(refs, assignment.track)}»),
          система сверяет его с содержимым и предупреждает при расхождении.
        </>
      }
    >
      <div className="flex flex-wrap items-end gap-2">
        <label className="text-xs">
          <span className="muted">Чья это работа</span>
          <select
            className="input mt-0.5 min-w-[220px]"
            value={studentId}
            onChange={(e) => setStudentId(e.target.value)}
          >
            {students.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </label>

        {/*
          Выбор версии показывается только когда у студента есть что
          заменять. Пустой список «Повторная сдача» рядом с выбором студента
          читался как список чего-то, что нужно выбрать перед загрузкой, —
          вопрос «это я выбираю виды критериев?» возник именно здесь.
        */}
        {existing.length > 0 && (
          <label className="text-xs">
            <span className="muted">Это новая работа или пересдача</span>
            <select
              className="input mt-0.5 min-w-[240px]"
              value={replaces}
              onChange={(e) => setReplaces(e.target.value)}
              title="Пересдача не создаёт вторую строку в очереди: предыдущая версия сохраняется для сравнения, проверяет её тот же ревьюер"
            >
              <option value="">новая работа</option>
              {existing.map((x) => (
                <option key={x.id} value={x.id}>
                  пересдача: {x.file_name} (версия {x.version})
                </option>
              ))}
            </select>
          </label>
        )}

        <label className="btn cursor-pointer">
          {busy ? "Загрузка…" : "Выбрать файлы"}
          <input
            ref={fileRef}
            type="file"
            multiple
            className="hidden"
            disabled={busy || !students.length}
            /* Список форматов берётся с сервера. Зашитый здесь короткий
               перечень скрывал половину поддерживаемого: диалог выбора
               файла не показывал ни .ipynb, ни .zip, ни исходный код. */
            accept={(refs?.formats ?? []).join(",") || undefined}
            onChange={(e) => e.target.files?.length && upload(e.target.files)}
          />
        </label>

        {!students.length && (
          <span className="text-xs" style={{ color: "var(--warn)" }}>
            Сначала добавьте студента в разделе «Участники».
          </span>
        )}
      </div>

      {warnings.length > 0 && (
        <ul className="mt-3 flex flex-col gap-1 text-xs" style={{ color: "var(--warn)" }}>
          {warnings.map((w, i) => (
            <li key={i}>! {w}</li>
          ))}
        </ul>
      )}
    </Section>
  );
}

/** Состав участников: ревьюеры с ёмкостью и компетенциями, студенты. */
export function People({
  refs,
  toast,
  onChanged,
}: {
  refs: Refs | null;
  toast: Toast;
  onChanged: () => void;
}) {
  const [users, setUsers] = useState<User[] | null>(null);
  const [adding, setAdding] = useState<"reviewer" | "student" | "coordinator" | null>(null);
  const [name, setName] = useState("");
  const [capacity, setCapacity] = useState(6);
  const [comps, setComps] = useState<string[]>(["product_fraud"]);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api.users().then(setUsers).catch(() => setUsers([]));
  }, []);
  useEffect(load, [load]);

  const submit = () => {
    if (!name.trim()) {
      toast("Укажите имя", "warn");
      return;
    }
    setBusy(true);
    api
      .createUser({
        name,
        role: adding!,
        capacity: adding === "reviewer" ? capacity : 10,
        competencies: adding === "reviewer" ? comps : [],
      })
      .then((u) => {
        toast(`Добавлен: ${u.name}`, "ok");
        setAdding(null);
        setName("");
        load();
        onChanged();
      })
      .catch((e) => toast(String(e.message ?? e), "err"))
      .finally(() => setBusy(false));
  };

  const reviewers = (users ?? []).filter((u) => u.role === "reviewer");
  const students = (users ?? []).filter((u) => u.role === "student");
  const coordinators = (users ?? []).filter((u) => u.role === "coordinator");

  return (
    <Section
      title="Участники"
      defaultOpen={false}
      right={
        <div className="flex flex-wrap gap-2">
          <button className="btn" onClick={() => setAdding("reviewer")}>
            + Ревьюер
          </button>
          <button className="btn" onClick={() => setAdding("student")}>
            + Студент
          </button>
          <button className="btn" onClick={() => setAdding("coordinator")}>
            + Методист
          </button>
        </div>
      }
    >
      {!users && <Spinner label="Загрузка…" />}

      {adding && (
        <div className="card mb-3">
          <div className="mb-2 text-sm font-medium">
            {{ reviewer: "Новый ревьюер", student: "Новый студент",
               coordinator: "Новый методист" }[adding]}
          </div>
          <div className="flex flex-wrap items-end gap-2">
            <label className="text-xs">
              <span className="muted">Имя</span>
              <input
                className="input mt-0.5 min-w-[220px]"
                value={name}
                placeholder="Иван Петров"
                onChange={(e) => setName(e.target.value)}
              />
            </label>
            {adding === "reviewer" && (
              <>
                <label className="text-xs">
                  <span className="muted">Ёмкость, работ за цикл</span>
                  <input
                    type="number"
                    min={1}
                    className="input mt-0.5 w-28"
                    value={capacity}
                    onChange={(e) => setCapacity(Number(e.target.value) || 1)}
                  />
                </label>
                <label className="text-xs">
                  <span className="muted">Компетенции (учитываются в распределении)</span>
                  <select
                    multiple
                    className="input mt-0.5 min-w-[240px]"
                    size={4}
                    value={comps}
                    onChange={(e) =>
                      setComps(Array.from(e.target.selectedOptions, (o) => o.value))
                    }
                  >
                    {(refs?.tracks ?? []).map((t) => (
                      <option key={t.id} value={t.id}>
                        {t.name}
                      </option>
                    ))}
                  </select>
                </label>
              </>
            )}
            <button className="btn btn-primary" disabled={busy} onClick={submit}>
              {busy ? "…" : "Добавить"}
            </button>
            <button className="btn" onClick={() => setAdding(null)}>
              Отмена
            </button>
          </div>
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-2">
        <div>
          <div className="muted mb-1 text-xs">Ревьюеры ({reviewers.length})</div>
          {reviewers.length === 0 && <Empty>Ревьюеров нет.</Empty>}
          <div className="flex flex-col gap-1">
            {reviewers.map((r) => (
              <div
                key={r.id}
                className="flex items-center gap-2 rounded px-2 py-1 text-xs"
                style={{ background: "var(--surface-2)" }}
              >
                <span className="font-medium">{r.name}</span>
                <Badge>ёмкость {r.capacity}</Badge>
                <span className="muted truncate">
                  {(
                    (r as any).competency_names ??
                    (r.competencies ?? []).map((c: string) => trackName(refs, c))
                  ).join(", ") || "без компетенций"}
                </span>
                <button
                  className="btn ml-auto text-[11px]"
                  title="Отключить: перестанет получать назначения, история сохранится"
                  onClick={() =>
                    api
                      .deactivateUser(r.id)
                      .then(() => {
                        toast(`${r.name} отключён`, "info");
                        load();
                        onChanged();
                      })
                      .catch((e) => toast(String(e.message ?? e), "err"))
                  }
                >
                  отключить
                </button>
              </div>
            ))}
          </div>
        </div>

        <div className="flex flex-col gap-3">
          <div>
            <div className="muted mb-1 text-xs">Студенты ({students.length})</div>
            {students.length === 0 && <Empty>Студентов нет.</Empty>}
            <div className="flex flex-col gap-1">
              {students.map((s) => (
                <div
                  key={s.id}
                  className="rounded px-2 py-1 text-xs"
                  style={{ background: "var(--surface-2)" }}
                >
                  {s.name}
                </div>
              ))}
            </div>
          </div>
          <div>
            <div className="muted mb-1 text-xs">Методисты ({coordinators.length})</div>
            <div className="flex flex-col gap-1">
              {coordinators.map((c) => (
                <div
                  key={c.id}
                  className="rounded px-2 py-1 text-xs"
                  style={{ background: "var(--surface-2)" }}
                >
                  {c.name}
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      <p className="muted mt-3 text-xs">
        Пользователи не удаляются, а отключаются: удаление оборвало бы ссылки в уже
        проверенных работах и сломало бы аналитику.
      </p>
    </Section>
  );
}
