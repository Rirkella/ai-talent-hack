/**
 * Вкладка методиста: критерии, сроки, распределение, аналитика, выгрузка.
 *
 * Порядок разделов повторяет сквозной сценарий, чтобы демонстрацию можно
 * было вести сверху вниз без переключений.
 */

import { Fragment, useCallback, useEffect, useState } from "react";

import { api, download, type User } from "../api";
import {
  ActionButton,
  Badge,
  Bar,
  Countdown,
  Empty,
  Hint,
  Section,
  Spinner,
  StatusBadge,
} from "../components/ui";
import Analytics from "./Analytics";
import { CreateAssignment, People, UploadSubmissions } from "./Manage";
import Privacy from "./Privacy";
import Scoring from "./Scoring";

type Toast = (t: string, tone?: "info" | "ok" | "warn" | "err") => void;

type Stage = "task" | "works" | "analytics" | "settings";

/** Этапы работы методиста — в том порядке, в каком их проходят. */
const STAGES: { key: Stage; title: string; hint: string }[] = [
  {
    key: "task",
    title: "1. Задание",
    hint: "Критерии оценивания и сроки. С этого начинается любое новое ДЗ.",
  },
  {
    key: "works",
    title: "2. Работы",
    hint: "Загрузка работ, распределение по ревьюерам, запуск проверки, выгрузка итогов.",
  },
  {
    key: "analytics",
    title: "3. Аналитика",
    hint: "Как идёт поток: воронка, баллы, слабые критерии, загрузка ревьюеров.",
  },
  {
    key: "settings",
    title: "Настройки",
    hint:
      "Участники потока, формула порядка ручной проверки и отчёт о защите " +
      "данных. Это управление стендом, а не работа с ДЗ.",
  },
];

export default function Coordinator({
  tick,
  toast,
  refresh,
}: {
  user: User;
  tick: number;
  toast: Toast;
  refresh: () => void;
}) {
  const [assignments, setAssignments] = useState<any[] | null>(null);
  const [current, setCurrent] = useState<string | null>(null);
  const [assignment, setAssignment] = useState<any>(null);
  const [subs, setSubs] = useState<any[]>([]);
  const [reviewers, setReviewers] = useState<User[]>([]);
  const [students, setStudents] = useState<User[]>([]);
  const [refs, setRefs] = useState<any>(null);
  const [localTick, setLocalTick] = useState(0);
  const [stage, setStage] = useState<Stage>("task");

  useEffect(() => {
    api.assignments().then((rows) => {
      setAssignments(rows);
      setCurrent((c) => (c && rows.some((r: any) => r.id === c) ? c : rows[0]?.id ?? null));
    });
    api.users().then((u) => {
      setReviewers(u.filter((x) => x.role === "reviewer"));
      setStudents(u.filter((x) => x.role === "student"));
    });
  }, [tick, localTick]);

  useEffect(() => {
    api.tracks().then(setRefs).catch(() => setRefs(null));
  }, []);

  const load = useCallback(() => {
    if (!current) return;
    api.assignment(current).then(setAssignment);
    api.submissions(current).then(setSubs);
  }, [current]);

  useEffect(load, [load, tick, localTick]);
  const bump = useCallback(() => setLocalTick((n) => n + 1), []);

  if (!assignments) return <Spinner label="Загрузка…" />;

  if (assignments.length === 0) {
    return (
      <div className="mx-auto max-w-2xl">
        <Empty>Заданий пока нет — создайте первое.</Empty>
        <CreateAssignment
          refs={refs}
          toast={toast}
          onCreated={(id) => {
            setCurrent(id);
            bump();
          }}
        />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <select
          className="input max-w-md"
          value={current ?? ""}
          onChange={(e) => setCurrent(e.target.value)}
        >
          {assignments.map((a) => (
            <option key={a.id} value={a.id}>
              {a.title} · {trackName(refs, a.track)} · работ: {a.submissions_count}
            </option>
          ))}
        </select>
        {assignment && (
          <Badge tone={assignment.rubric_approved ? "ok" : "warn"}>
            {assignment.rubric_approved
              ? "критерии утверждены"
              : "критерии не утверждены — проверка не запустится"}
          </Badge>
        )}
        <div className="ml-auto flex items-center gap-2">
          <CreateAssignment
            refs={refs}
            toast={toast}
            onCreated={(id) => {
              setCurrent(id);
              bump();
            }}
          />
        <button
          className="btn"
          onClick={() =>
            api
              .reset()
              .then(() => toast("Демо-данные сброшены. Выполните seed заново.", "info"))
              .then(refresh)
              .catch((e) => toast(String(e.message ?? e), "err"))
          }
          title="Очистить работы, результаты проверок и уведомления для повторного прогона сценария"
        >
          Сбросить демо
        </button>
        </div>
      </div>

      <HowTo />

      {/*
        Разделы разложены по этапам работы, а не свалены в одну ленту.
        Экран методиста был высотой почти четыре тысячи пикселей: восемнадцать
        блоков подряд, шесть развёрнутых. Найти нужный можно было только
        прокруткой, и по виду страницы не читалось, что за чем делать.
      */}
      {assignment && (
        <>
          <nav className="flex flex-wrap gap-1">
            {STAGES.map(({ key, title, hint }) => (
              <button
                key={key}
                className="btn"
                onClick={() => setStage(key)}
                title={hint}
                style={
                  stage === key
                    ? { borderColor: "var(--brand)", color: "var(--brand)" }
                    : undefined
                }
              >
                {title}
              </button>
            ))}
          </nav>
          <p className="muted -mt-2 text-xs">
            {STAGES.find((s) => s.key === stage)?.hint}
          </p>

          {stage === "task" && (
            <>
              <RubricPanel assignment={assignment} toast={toast} onChanged={load} />
              <DeadlinePanel assignment={assignment} toast={toast} onChanged={load} />
            </>
          )}

          {stage === "works" && (
            <>
              <UploadSubmissions
                assignment={assignment}
                students={students}
                refs={refs}
                toast={toast}
                onChanged={() => {
                  load();
                  refresh();
                }}
              />
              <AllocationPanel
                assignment={assignment}
                subs={subs}
                reviewers={reviewers}
                toast={toast}
                onChanged={load}
              />
              <SubmissionsTable
                subs={subs}
                reviewers={reviewers}
                assignment={assignment}
                toast={toast}
                onChanged={load}
              />
              <SimilarityPanel assignmentId={assignment.id} toast={toast} />
            </>
          )}

          {stage === "analytics" && <Analytics assignmentId={assignment.id} tick={tick} />}

          {stage === "settings" && (
            <>
              <People refs={refs} toast={toast} onChanged={bump} />
              <Scoring assignmentId={assignment.id} toast={toast} onChanged={load} />
              {/*
                Отчёт о защите данных. Вкладкой в шапке он висел у всех
                ролей, хотя ревьюеру не нужен ни разу за проверку работы:
                это состояние контура, то есть настройка стенда.
                Своей обёртки-карточки не получает — внутри уже карточки,
                и вложение дало бы рамку в рамке.
              */}
              <div className="mt-1 flex items-center gap-2">
                <h3 className="font-semibold">Защита данных и офлайн-контур</h3>
                <Hint text="Сколько персональных данных заменено псевдонимами перед отправкой в модель и сколько было обращений за пределы контура. Отвечает на вопрос «куда уходят работы студентов» — никуда." />
              </div>
              <Privacy tick={tick} />
            </>
          )}
        </>
      )}
    </div>
  );
}

/** Человеческое название направления по его идентификатору. */
export function trackName(refs: any, id: string): string {
  return (refs?.tracks ?? []).find((t: any) => t.id === id)?.name ?? id;
}

/**
 * Порядок действий для нового задания.
 *
 * Экран длинный, и по нему не видно, что за чем делать: критерии, сроки,
 * загрузка и распределение выглядят как равноправные блоки, а на деле
 * это последовательность, где второй шаг не работает без первого.
 * Свёрнут по умолчанию, чтобы не мешать тем, кто уже разобрался.
 */
function HowTo() {
  const steps: [string, string][] = [
    ["1. Создать задание", "Кнопка «+ Новое задание» наверху: название, курс, направление."],
    [
      "2. Задать критерии",
      "Блок «Критерии оценивания»: загрузить файл условия или написать требования текстом. Это то, за что выставляются баллы.",
    ],
    [
      "3. Утвердить критерии",
      "Кнопка «Утвердить критерии». Пока не утверждены — проверка не запускается: ошибка в критериях исказила бы все баллы разом.",
    ],
    ["4. Проверить сроки", "Блок «Сроки и штрафы». Если срок был в условии, он уже заполнен — сверьте год."],
    [
      "5. Получить работы",
      "Студент сдаёт сам со своей вкладки, либо загрузите пачкой в блоке «Загрузка работ».",
    ],
    [
      "6. Раздать ревьюерам",
      "Блок «Распределение»: работы уходят с учётом загрузки и компетенций. Дальше проверяет ревьюер.",
    ],
  ];

  return (
    <Section
      title="С чего начать: порядок действий"
      hint="Шесть шагов от пустого экрана до проверенной работы. Руководство со скриншотами и сценарий показа — docs/guide.md"
      defaultOpen={false}
    >
      <ol className="flex flex-col gap-2">
        {steps.map(([title, text]) => (
          <li key={title} className="text-sm">
            <span className="font-medium">{title}.</span>{" "}
            <span className="muted">{text}</span>
          </li>
        ))}
      </ol>
    </Section>
  );
}

/** Критерии оценивания: три способа задать и обязательное утверждение человеком. */
export function RubricPanel({
  assignment,
  toast,
  onChanged,
  canApprove = true,
}: {
  assignment: any;
  toast: Toast;
  onChanged: () => void;
  /** Утверждает только методист: это точка, где решение принимает человек,
      отвечающий за курс. Ревьюер критерии задать может, утвердить — нет. */
  canApprove?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  // Ввод требований текстом — третий способ наравне с файлом. Не у всякого
  // задания есть файл условия: требования часто живут в письме или в голове
  // методиста, и раньше их нельзя было внести иначе как правкой JSON.
  const [textMode, setTextMode] = useState(false);
  const [conditionText, setConditionText] = useState("");

  const rubric = assignment.rubric ?? {};
  const criteria = rubric.criteria ?? [];

  const applyResult = (r: any) => {
    toast(
      r.ok
        ? `Найдено критериев: ${r.rubric.criteria.length}. Проверьте и утвердите.`
        : `Разобрать не удалось: ${r.error}. Введите критерии вручную.`,
      r.ok ? "ok" : "warn",
    );
    onChanged();
  };

  const upload = (file: File) => {
    setBusy(true);
    api
      .uploadCondition(assignment.id, file)
      .then(applyResult)
      .catch((e) => toast(String(e.message ?? e), "err"))
      .finally(() => setBusy(false));
  };

  return (
    <Section
      title="Критерии оценивания"
      hint={
        <>
          Критерии — это то, за что выставляются баллы. Задать их можно тремя
          способами: загрузить файл условия, написать требования текстом или
          править вручную. Пока критерии не утверждены, проверка не
          запускается: ошибка в критериях исказила бы все баллы разом.
        </>
      }
      right={
        <div className="flex flex-wrap items-center gap-2">
          {canApprove && !assignment.rubric_approved && criteria.length > 0 && (
            <ActionButton
              primary
              doneLabel="Утверждено"
              onAction={() =>
                api
                  .approveRubric(assignment.id)
                  .then(() => {
                    toast("Критерии утверждены — проверку можно запускать", "ok");
                    onChanged();
                  })
                  .catch((e) => {
                    toast(String(e.message ?? e), "err");
                    throw e;
                  })
              }
            >
              Утвердить критерии
            </ActionButton>
          )}
          <button className="btn" onClick={() => setTextMode((v) => !v)}>
            {textMode ? "Скрыть ввод текстом" : "Написать текстом"}
          </button>
          <label className="btn cursor-pointer">
            {busy ? "Читаю файл…" : "Загрузить файл условия"}
            <input
              type="file"
              className="hidden"
              accept=".pdf,.docx,.md,.txt"
              disabled={busy}
              onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])}
            />
          </label>
        </div>
      }
    >
      {textMode && (
        <div className="mb-3 rounded p-3" style={{ background: "var(--surface-2)" }}>
          <div className="mb-1 text-sm font-medium">Требования текстом</div>
          <p className="muted mb-2 text-xs">
            Вставьте условие задания как есть или напишите критерии списком —
            по строке на критерий, с баллом в скобках. Система разберёт текст
            и покажет критерии для проверки. Пример:
            <br />
            <code>Корректность описания продукта (2 балла)</code>
            <br />
            <code>Глубина анализа рисков (4 балла)</code>
          </p>
          <textarea
            className="input min-h-[160px] text-sm"
            placeholder="Вставьте сюда текст условия или список критериев с баллами…"
            value={conditionText}
            onChange={(e) => setConditionText(e.target.value)}
          />
          <div className="mt-2 flex items-center gap-2">
            <ActionButton
              primary
              doneLabel="Разобрано"
              disabled={conditionText.trim().length < 40}
              onAction={() =>
                api
                  .uploadConditionText(assignment.id, conditionText)
                  .then((r) => {
                    applyResult(r);
                    if (r.ok) setTextMode(false);
                  })
                  .catch((e) => {
                    toast(String(e.message ?? e), "err");
                    throw e;
                  })
              }
            >
              Разобрать текст
            </ActionButton>
            <span className="muted text-xs">
              {conditionText.trim().length < 40
                ? "нужно хотя бы 40 символов"
                : `${conditionText.trim().length} символов`}
            </span>
          </div>
        </div>
      )}

      {rubric.notes && (
        <div className="mb-3 rounded p-2 text-xs" style={{ background: "var(--surface-2)", color: "var(--warn)" }}>
          {rubric.notes}
        </div>
      )}

      {criteria.length === 0 && (
        <Empty>
          Критериев пока нет. Загрузите файл условия, напишите требования
          текстом или введите критерии вручную.
        </Empty>
      )}

      {criteria.length > 0 && !editing && (
        <>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-left muted" style={{ borderColor: "var(--border)" }}>
                <th className="py-1 font-normal">Критерий и что считается идеальным ответом</th>
                <th className="w-24 py-1 text-right font-normal">Максимум</th>
                <th className="w-20 py-1 text-right font-normal">Вес</th>
              </tr>
            </thead>
            <tbody>
              {criteria.map((c: any) => (
                <tr key={c.id} className="border-b last:border-0" style={{ borderColor: "var(--border)" }}>
                  <td className="py-1.5">
                    <div>{c.name}</div>
                    {/*
                      Текст требований показывается целиком. Обрезка по 160
                      символам съедала ровно ту часть, ради которой его и
                      читают: у критерия «Реалистичный расчёт ROI» видно было
                      начало «Выбран один из трёх приоритетных рисков…»,
                      а сама формула ROI не помещалась.
                    */}
                    {c.requirements && (
                      <div className="muted mt-0.5 whitespace-pre-wrap text-xs">
                        {c.requirements}
                      </div>
                    )}
                    {/*
                      Признаки — под раскрывашкой. Пять критериев по
                      четыре-шесть признаков давали шестьдесят строк подряд,
                      и таблица критериев превращалась в стену текста, где
                      не видно ни названий, ни баллов.
                    */}
                    {c.indicators?.length > 0 && (
                      <details className="mt-1">
                        <summary className="muted cursor-pointer text-xs">
                          признаки, по которым проверяется ({c.indicators.length})
                        </summary>
                        <ul className="muted mt-1 list-disc pl-4 text-xs">
                          {c.indicators.map((ind: string, i: number) => (
                            <li key={i}>{ind}</li>
                          ))}
                        </ul>
                      </details>
                    )}
                  </td>
                  <td className="py-1.5 text-right align-top tabular-nums">{c.max_score}</td>
                  <td className="py-1.5 text-right align-top tabular-nums">{c.weight}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="mt-2 flex flex-wrap items-center gap-3 text-sm">
            <span>
              Сумма максимумов:{" "}
              <b className="tabular-nums">
                {rubric.criteria.reduce((a: number, c: any) => a + c.max_score, 0)}
              </b>
            </span>
            <FormalSummary rubric={rubric} />
            <button
              className="btn ml-auto"
              onClick={() => {
                setDraft(JSON.stringify(rubric, null, 2));
                setEditing(true);
              }}
            >
              Править вручную
            </button>
          </div>
        </>
      )}

      {editing && (
        <div>
          <textarea
            className="input min-h-[320px] font-mono text-xs"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
          />
          <div className="mt-2 flex gap-2">
            <button
              className="btn btn-primary"
              onClick={() => {
                try {
                  const parsed = JSON.parse(draft);
                  api
                    .updateRubric(assignment.id, parsed)
                    .then(() => {
                      toast("Критерии сохранены. Утверждение сброшено — подтвердите заново.", "ok");
                      setEditing(false);
                      onChanged();
                    })
                    .catch((e) => toast(String(e.message ?? e), "err"));
                } catch {
                  toast("Это не похоже на корректный JSON — проверьте скобки и запятые", "err");
                }
              }}
            >
              Сохранить
            </button>
            <button className="btn" onClick={() => setEditing(false)}>
              Отмена
            </button>
          </div>
        </div>
      )}
    </Section>
  );
}

/**
 * Формальные требования одной строкой.
 *
 * Печатается только то, что действительно задано условием. Раньше строка
 * собиралась безусловно и на курсе, где про шрифт ничего не сказано,
 * выглядела как «формальные требования: null стр., undefined null пт» —
 * то есть сообщала о требованиях, которых нет.
 */
function FormalSummary({ rubric }: { rubric: any }) {
  const parts: string[] = [];
  if (rubric.max_pages) parts.push(`объём до ${rubric.max_pages} стр.`);
  if (rubric.body_font || rubric.body_size_pt) {
    parts.push(
      `текст ${[rubric.body_font, rubric.body_size_pt && `${rubric.body_size_pt} пт`]
        .filter(Boolean)
        .join(" ")}`,
    );
  }
  if (rubric.table_font || rubric.table_size_pt) {
    parts.push(
      `таблицы ${[rubric.table_font, rubric.table_size_pt && `${rubric.table_size_pt} пт`]
        .filter(Boolean)
        .join(" ")}`,
    );
  }
  if (rubric.line_spacing_min || rubric.line_spacing_max) {
    parts.push(`интервал ${rubric.line_spacing_min}–${rubric.line_spacing_max}`);
  }
  if (rubric.allowed_formats?.length) {
    parts.push(`форматы ${rubric.allowed_formats.join(", ")}`);
  }

  return (
    <span className="muted text-xs">
      {parts.length
        ? `требования к оформлению: ${parts.join(", ")}`
        : "требований к оформлению условие не задаёт — эти проверки будут помечены «не применимо»"}
    </span>
  );
}

/**
 * Панель сроков.
 *
 * Часы системы не трогаются: время всегда настоящее, управляются сами
 * дедлайны. Пресеты в секундах нужны, чтобы переход состояний происходил
 * на глазах у зрителей.
 */
function DeadlinePanel({
  assignment,
  toast,
  onChanged,
}: {
  assignment: any;
  toast: Toast;
  onChanged: () => void;
}) {
  const set = (body: Record<string, unknown>) =>
    api
      .setDeadlines(assignment.id, body)
      .then(() => toast("Сроки обновлены", "ok"))
      .then(onChanged)
      .catch((e) => toast(String(e.message ?? e), "err"));

  const shift = (field: string, seconds: number) =>
    set({ [field]: new Date(Date.now() + seconds * 1000).toISOString() });

  const rows: [string, string, string][] = [
    ["due_at", "Мягкий дедлайн", "срок сдачи; после него работа принимается со штрафом"],
    ["hard_due_at", "Жёсткий дедлайн", "после него работа оценивается в 0 баллов"],
    ["review_due_at", "Срок проверки", "проверка занимает не более 7 дней после дедлайна"],
  ];

  return (
    <Section
      title="Сроки и штрафы"
      hint={
        <>
          Дедлайн сдачи, окно опоздания и правило штрафа из условия — отсюда же
          берутся напоминания. Правило взято из условия задания: досдача в
          течение суток — штраф −1 балл, после жёсткого срока — 0 баллов.
          Системные часы не подкручиваются: время настоящее, двигаются только
          сами сроки.
        </>
      }
    >
      <table className="w-full text-sm">
        <tbody>
          {rows.map(([field, title, hint]) => (
            <tr key={field} className="border-b last:border-0" style={{ borderColor: "var(--border)" }}>
              <td className="py-2">
                <div className="font-medium">{title}</div>
                <div className="muted text-xs">{hint}</div>
              </td>
              <td className="py-2">
                <input
                  type="datetime-local"
                  step={1}
                  className="input w-56"
                  value={toLocalInput(assignment[field])}
                  onChange={(e) =>
                    e.target.value && set({ [field]: new Date(e.target.value).toISOString() })
                  }
                />
              </td>
              <td className="py-2 text-sm">
                <Countdown to={assignment[field]} />
              </td>
              <td className="py-2 text-right">
                <div className="flex justify-end gap-1">
                  {[
                    ["+10 с", 10],
                    ["+30 с", 30],
                    ["+1 мин", 60],
                    ["+5 мин", 300],
                  ].map(([label, s]) => (
                    <button
                      key={label as string}
                      className="btn text-xs"
                      onClick={() => shift(field, s as number)}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <ActionButton
          primary
          doneLabel="Идёт отсчёт"
          onAction={() =>
            api
              .setDemoDeadlines(assignment.id, 10, 40)
              .then(() => {
                toast(
                  "Мягкий срок через 10 с, жёсткий через 40 с. Следите за состояниями.",
                  "ok",
                );
                onChanged();
              })
              .catch((e) => {
                toast(String(e.message ?? e), "err");
                throw e;
              })
          }
        >
          Режим демонстрации: 10 с / 40 с
        </ActionButton>
        {/*
          Обратный ход обязателен. Пресет ставит сроки в десять и сорок
          секунд; они проходят, и дальше у всех работ висит «просрочено»,
          а три момента времени в панели различаются на секунды. Ровно так
          демо-данные и выглядели после первого же показа правила штрафа.
        */}
        <ActionButton
          doneLabel="Сроки обычные"
          title="Сдача через двое суток, окно опоздания сутки, проверка неделя"
          onAction={() =>
            api
              .setNormalDeadlines(assignment.id)
              .then(() => {
                toast("Обычные сроки восстановлены: сдача через 2 дня.", "ok");
                onChanged();
              })
              .catch((e) => {
                toast(String(e.message ?? e), "err");
                throw e;
              })
          }
        >
          Вернуть обычные сроки
        </ActionButton>
        <span className="muted text-xs">
          Тот же механизм обслуживает и реальные сроки в днях. Режим
          демонстрации нужен, чтобы переход в штрафную зону произошёл на
          глазах у зрителей, а не через сутки.
        </span>
      </div>
    </Section>
  );
}

function toLocalInput(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

/** Распределение по нагрузке с графиком «до/после». */
function AllocationPanel({
  assignment,
  subs,
  reviewers,
  toast,
  onChanged,
}: {
  assignment: any;
  subs: any[];
  reviewers: User[];
  toast: Toast;
  onChanged: () => void;
}) {
  const [result, setResult] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const pending = subs.filter((s) => !s.reviewer_id).length;
  // В счётчике только те работы, которые реально уйдут в очередь. Раньше
  // считались все строки, включая нагрузку прошлого потока без файла:
  // кнопка обещала «Проверить все (6)», а в очередь уходило три.
  const checkable = subs.filter((s) => s.has_file !== false).length;

  return (
    <Section
      title="Распределение по ревьюерам"
      hint={
        <>
          Каждая работа уходит <b>одному</b> ревьюеру. Кому именно — решает
          венгерский алгоритм: он перебирает не работы по очереди, а все
          сочетания сразу и выбирает то, где суммарная «стоимость»
          наименьшая. В стоимость входят компетенция по направлению,
          текущая загрузка относительно ёмкости, срочность и объём работы.
          Кто что получил — в таблице ниже, а не в общей сводке.
        </>
      }
      right={
        <div className="flex flex-wrap gap-2">
          <button
            className="btn"
            disabled={!assignment.rubric_approved || checkable === 0}
            title={assignment.rubric_approved ? "" : "Сначала утвердите критерии оценивания"}
            onClick={() =>
              api
                .reviewAll(assignment.id)
                .then((r) => toast(`Поставлено в очередь: ${r.queued}`, "ok"))
                .then(onChanged)
                .catch((e) => toast(String(e.message ?? e), "err"))
            }
          >
            Проверить все ({checkable})
          </button>
          <button
            className="btn btn-primary"
            disabled={busy || pending === 0}
            onClick={() => {
              setBusy(true);
              api
                .allocate(assignment.id)
                .then((r) => {
                  setResult(r);
                  toast(`Распределено: ${r.assigned}`, "ok");
                  onChanged();
                })
                .catch((e) => toast(String(e.message ?? e), "err"))
                .finally(() => setBusy(false));
            }}
          >
            {busy ? "Распределение…" : `Распределить (${pending})`}
          </button>
        </div>
      }
    >
      {/*
        Список назначений — то, ради чего нажимают кнопку. Раньше здесь были
        только столбики нагрузки по всем ревьюерам и два агрегата, а сам
        ответ «работа → ревьюер» приходил с сервера и выбрасывался. Со
        стороны это читалось так, будто одна работа ушла ко всем сразу.
      */}
      {result?.allocations?.length > 0 && (
        <div className="mb-3">
          <div className="mb-1 text-sm font-medium">Кто что получил</div>
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b text-left muted" style={{ borderColor: "var(--border)" }}>
                <th className="py-1 font-normal">Работа</th>
                <th className="py-1 font-normal">Ревьюер</th>
                <th className="py-1 font-normal">Почему он</th>
              </tr>
            </thead>
            <tbody>
              {result.allocations.map((al: any) => {
                const work = subs.find((s) => s.id === al.work_id);
                const reviewer = reviewers.find((r) => r.id === al.reviewer_id);
                return (
                  <tr key={al.work_id} className="border-b last:border-0"
                      style={{ borderColor: "var(--border)" }}>
                    <td className="py-1 pr-2">
                      <div>{work?.student_name ?? al.work_id}</div>
                      <div className="muted truncate" title={work?.file_name}>
                        {work?.file_name}
                      </div>
                    </td>
                    <td className="py-1 pr-2">
                      {reviewer?.name ?? (
                        <span style={{ color: "var(--err)" }}>не назначен</span>
                      )}
                    </td>
                    <td className="py-1 muted">{(al.reasons ?? []).join("; ")}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <div className="mb-2 flex items-center gap-2">
        <span className="text-sm font-medium">Загрузка ревьюеров</span>
        <Hint
          text={
            <>
              Столбик на каждого ревьюера: сколько работ у него было и сколько
              стало. Ревьюеры, которым ничего не досталось, показаны тоже —
              чтобы было видно, что свободные силы остались.
            </>
          }
        />
      </div>
      <LoadChart
        reviewers={reviewers}
        before={result?.load_before}
        after={result?.load_after ?? currentLoad(subs, reviewers)}
      />

      {result && (
        <div className="mt-3 text-xs">
          <div className="flex flex-wrap gap-4">
            <span>
              Максимум на ревьюера: <b>{result.max_before}</b> → <b>{result.max_after}</b>
            </span>
            <span>
              Ровность загрузки: <b>{(result.balance_before * 100).toFixed(0)}%</b> →{" "}
              <b>{(result.balance_after * 100).toFixed(0)}%</b>
            </span>
          </div>
          {result.notes?.map((n: string, i: number) => (
            <div key={i} className="mt-1" style={{ color: "var(--warn)" }}>
              {n}
            </div>
          ))}
        </div>
      )}
    </Section>
  );
}

function currentLoad(subs: any[], reviewers: User[]): Record<string, number> {
  const out: Record<string, number> = {};
  for (const r of reviewers) out[r.id] = 0;
  for (const s of subs) if (s.reviewer_id) out[s.reviewer_id] = (out[s.reviewer_id] ?? 0) + 1;
  return out;
}

/**
 * График загрузки «до/после».
 *
 * Написан инлайновым SVG, а не библиотекой: две группы столбцов не стоят
 * зависимости, а контур офлайн — лишний пакет только увеличил бы бандл.
 */
function LoadChart({
  reviewers,
  before,
  after,
}: {
  reviewers: User[];
  before?: Record<string, number>;
  after?: Record<string, number>;
}) {
  if (!reviewers.length) return <Empty>Ревьюеров нет.</Empty>;
  const max = Math.max(
    1,
    ...reviewers.map((r) =>
      Math.max(r.capacity ?? 0, before?.[r.id] ?? 0, after?.[r.id] ?? 0),
    ),
  );

  return (
    <div className="flex flex-col gap-2">
      {reviewers.map((r) => {
        const b = before?.[r.id] ?? 0;
        const a = after?.[r.id] ?? 0;
        const cap = r.capacity ?? 10;
        return (
          <div key={r.id} className="text-xs">
            <div className="mb-0.5 flex justify-between">
              <span>
                {r.name}{" "}
                <span className="muted">
                  · ёмкость {cap} ·{" "}
                  {((r as any).competency_names ?? r.competencies ?? []).join(", ") ||
                    "без компетенций"}
                </span>
              </span>
              <span className="tabular-nums">
                {before ? `${b} → ` : ""}
                <b>{a}</b> / {cap}
              </span>
            </div>
            <div className="flex flex-col gap-0.5">
              {before && (
                <div className="h-2 w-full rounded" style={{ background: "var(--surface-2)" }}>
                  <div
                    className="h-full rounded"
                    style={{ width: `${(b / max) * 100}%`, background: "var(--text-dim)" }}
                    title={`было: ${b}`}
                  />
                </div>
              )}
              <div className="h-2.5 w-full rounded" style={{ background: "var(--surface-2)" }}>
                <div
                  className="h-full rounded"
                  style={{
                    width: `${(a / max) * 100}%`,
                    background: a > cap ? "var(--err)" : "var(--brand)",
                  }}
                  title={`стало: ${a}`}
                />
              </div>
            </div>
          </div>
        );
      })}
      {before && (
        <div className="muted text-[11px]">серый — до распределения, синий — после</div>
      )}
    </div>
  );
}

/**
 * Все работы задания одной таблицей: состояние, срок, балл, ревьюер.
 *
 * Здесь же запускается предварительная проверка и открывается её разбор.
 * Раньше разбор жил только на экране ревьюера, и методист видел балл,
 * но не видел, откуда он взялся: «8 из 10» без единого основания.
 */
function SubmissionsTable({
  subs,
  reviewers,
  assignment,
  toast,
  onChanged,
}: {
  subs: any[];
  reviewers: User[];
  assignment: any;
  toast: Toast;
  onChanged: () => void;
}) {
  // Какая строка раскрыта разбором. Одна за раз: разбор длинный, и две
  // раскрытые строки подряд снова превращают таблицу в ленту текста.
  const [openId, setOpenId] = useState<string | null>(null);
  const [withHistory, setWithHistory] = useState(false);
  const real = subs.filter((s) => s.has_file !== false && !s.superseded);

  return (
    <Section
      title={`Работы (${real.length})`}
      hint={
        <>
          Все работы этого задания. Кнопка «Проверить» ставит работу в очередь
          к модели: она читает файл и выставляет предварительные баллы по
          критериям. Кнопка неактивна, если критерии ещё не утверждены или к
          работе не приложен файл — причина написана в строке. «Разбор»
          показывает, что именно нашла модель и чем подтвердила каждый балл.
        </>
      }
      right={
        <div className="flex flex-wrap items-center gap-2">
          {/*
            Два режима выгрузки. Обычный — ведомость: актуальные версии
            реально сданных работ, по одной строке на студента. Раньше в
            файл шло всё подряд, включая занятые места без файлов и прежние
            версии пересданных работ: три файла превращались в пять строк,
            после пересдачи — в шесть, и выставить по такому файлу оценки
            было нельзя.
          */}
          <label className="muted flex items-center gap-1 text-xs">
            <input
              type="checkbox"
              checked={withHistory}
              onChange={(e) => setWithHistory(e.target.checked)}
            />
            с историей версий
          </label>
          {(["xlsx", "csv", "json"] as const).map((fmt) => (
            <ActionButton
              key={fmt}
              doneLabel="Скачано"
              title={
                withHistory
                  ? "Все версии всех работ, включая заменённые"
                  : fmt === "json"
                    ? "Те же строки в JSON — для передачи в другую систему"
                    : `Ведомость в ${fmt.toUpperCase()}: по строке на актуальную работу`
              }
              onAction={() =>
                download(
                  api.exportUrl(assignment.id, fmt, withHistory),
                  `review_${assignment.track}.${fmt}`,
                ).catch((e) => {
                  toast(String(e.message ?? e), "err");
                  throw e;
                })
              }
            >
              {fmt.toUpperCase()}
            </ActionButton>
          ))}
        </div>
      }
    >
      {!assignment.rubric_approved && (
        <div
          className="mb-2 rounded p-2 text-xs"
          style={{ background: "var(--surface-2)", color: "var(--warn)" }}
        >
          Критерии оценивания не утверждены — проверка не запустится ни по
          одной работе. Утвердите их на шаге «1. Задание».
        </div>
      )}
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b text-left muted" style={{ borderColor: "var(--border)" }}>
              <th className="py-1 font-normal">Студент</th>
              <th className="py-1 font-normal">Файл</th>
              <th className="py-1 font-normal">Срок сдачи</th>
              <th className="py-1 font-normal">Состояние</th>
              <th className="px-3 py-1 text-right font-normal">Балл</th>
              <th
                className="px-3 py-1 text-right font-normal"
                title="Порядок ручной проверки: 1 — смотреть первой"
              >
                Очередь
              </th>
              <th className="py-1 font-normal">Ревьюер</th>
              <th className="py-1"></th>
            </tr>
          </thead>
          <tbody>
            {real.map((s) => (
              <Fragment key={s.id}>
                <tr className="border-b" style={{ borderColor: "var(--border)" }}>
                  <td className="py-1.5">{s.student_name}</td>
                  <td className="max-w-[220px] truncate py-1.5" title={s.file_name}>
                    {s.file_name}
                  </td>
                  <td className="py-1.5">
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
                  </td>
                  <td className="py-1.5">
                    <StatusBadge status={s.status} />
                  </td>
                  <td className="px-3 py-1.5 text-right tabular-nums">
                    {s.final_score ?? s.preliminary_score ?? "—"}
                    {s.final_score != null && (
                      <span className="muted" title="Балл подтверждён ревьюером">
                        {" "}
                        ✓
                      </span>
                    )}
                  </td>
                  <td
                    className="px-3 py-1.5 text-right tabular-nums"
                    title="Насколько работа нуждается в ручной проверке: 0 — вероятнее всего согласиться, 1 — смотреть первой"
                  >
                    {s.priority_index != null ? s.priority_index.toFixed(2) : "—"}
                  </td>
                  <td className="py-1.5">
                    <select
                      className="input py-0.5 text-xs"
                      value={s.reviewer_id ?? ""}
                      onChange={(e) =>
                        api
                          .reassign(s.id, e.target.value)
                          .then(() => toast("Работа перенесена", "ok"))
                          .then(onChanged)
                          .catch((err) => toast(String(err.message ?? err), "err"))
                      }
                    >
                      <option value="">— не назначен —</option>
                      {reviewers.map((r) => (
                        <option key={r.id} value={r.id}>
                          {r.name}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td className="whitespace-nowrap py-1.5 text-right">
                    <div className="flex justify-end gap-1">
                      {s.review_available && (
                        <button
                          className="btn text-xs"
                          onClick={() => setOpenId(openId === s.id ? null : s.id)}
                          title="Показать, что нашла модель: баллы по критериям, цитаты, формальные нарушения"
                        >
                          {openId === s.id ? "Скрыть разбор" : "Разбор"}
                        </button>
                      )}
                      <ActionButton
                        className="text-xs"
                        doneLabel="В очереди"
                        disabled={!assignment.rubric_approved}
                        title={
                          !assignment.rubric_approved
                            ? "Сначала утвердите критерии оценивания"
                            : s.review_available
                              ? "Проверить заново: модель перечитает файл и пересчитает баллы"
                              : "Запустить предварительную проверку моделью"
                        }
                        onAction={() =>
                          api
                            .startReview(s.id)
                            .then(() => {
                              toast(
                                "Работа поставлена в очередь. Проверка занимает "
                                  + "около минуты, результат придёт уведомлением.",
                                "ok",
                              );
                              onChanged();
                            })
                            .catch((e) => {
                              toast(String(e.message ?? e), "err");
                              throw e;
                            })
                        }
                      >
                        {s.review_available ? "Проверить заново" : "Проверить"}
                      </ActionButton>
                    </div>
                  </td>
                </tr>
                {openId === s.id && (
                  <tr style={{ borderColor: "var(--border)" }}>
                    <td colSpan={8} className="pb-3">
                      <ReviewDigest submissionId={s.id} />
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>

      {/*
        Строки без файла — нагрузка прошлого потока, занятое место в очереди
        ревьюера. Раньше они стояли в общей таблице наравне со сдачами, и у
        них было всё, чего у них быть не может: имя студента, «сдано в срок»,
        приоритет 0,00 и выключенная кнопка «Проверить» без видимой причины.
      */}
      {subs.length > real.length && (
        <p className="muted mt-3 text-xs">
          Кроме них в очереди ревьюеров занято мест: {subs.length - real.length}.
          Это работы прошлого потока без файлов — они учитываются при
          распределении нагрузки, но проверять в них нечего.
        </p>
      )}

      <p className="muted mt-3 text-xs">
        Выгрузка (кнопки справа наверху) заменяет ручной перенос результатов в
        таблицу проверки. Внешних интеграций нет — контур офлайн, файл
        открывается той же таблицей.
      </p>
    </Section>
  );
}

/**
 * Разбор проверки: за что модель поставила баллы и чем их подтвердила.
 *
 * Только чтение. Менять баллы вправе ревьюер, которому работа назначена, —
 * это единственное место, где решение принимает человек, и делать вторую
 * точку редактирования у методиста нельзя.
 */
function ReviewDigest({ submissionId }: { submissionId: string }) {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    setData(null);
    setError("");
    api
      .submission(submissionId)
      .then(setData)
      .catch((e) => setError(String(e.message ?? e)));
  }, [submissionId]);

  if (error) return <div style={{ color: "var(--err)" }}>{error}</div>;
  if (!data) return <Spinner label="Загрузка разбора…" />;

  const review = data.review;
  if (!review) return <Empty>Проверка ещё не выполнялась.</Empty>;

  const formal = review.formal ?? [];
  const failed = formal.filter((f: any) => f.status === "fail");
  // Статусов четыре, а не два: «не определено» — это не «нарушений нет».
  // Межстрочный интервал наследуется из стилей, и когда его не видно,
  // честный ответ — «не определено».
  const unknown = formal.filter((f: any) => f.status === "unknown");
  const ai = review.ai_signal ?? {};

  return (
    <div className="rounded p-3" style={{ background: "var(--surface-2)" }}>
      <div className="mb-2 flex flex-wrap items-baseline gap-3">
        <span className="text-sm font-semibold">
          Предварительно {review.preliminary_score} из {review.max_score}
        </span>
        <span className="muted">
          проверено за {Math.round((review.duration_ms ?? 0) / 1000)} с, модель{" "}
          {review.model}
        </span>
      </div>

      <table className="w-full">
        <thead className="muted text-left">
          <tr>
            <th className="w-1/3 py-1 font-normal">Критерий</th>
            <th className="py-1 text-right font-normal">Балл</th>
            <th className="py-1 pl-3 font-normal">Обоснование и доказательства</th>
          </tr>
        </thead>
        <tbody>
          {(review.criteria ?? []).map((c: any) => {
            const verified = (c.evidence ?? []).filter(
              (e: any) => e.status === "verified",
            ).length;
            return (
              <tr
                key={c.criterion_id}
                className="border-t align-top"
                style={{ borderColor: "var(--border)" }}
              >
                <td className="py-1.5 pr-2">{c.criterion_name}</td>
                <td className="py-1.5 text-right tabular-nums">
                  {c.score} / {c.max_score}
                </td>
                <td className="py-1.5 pl-3">
                  <div>{c.verdict}</div>
                  <div className="muted mt-0.5">
                    {(c.evidence ?? []).length === 0
                      ? "цитат нет"
                      : `цитат ${(c.evidence ?? []).length}, подтверждено кодом ${verified}`}
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      <div className="mt-3 flex flex-wrap gap-4">
        <div>
          <div className="muted">Формальные требования</div>
          <div className="flex flex-wrap gap-1">
            {failed.length === 0 && <Badge tone="ok">нарушений не найдено</Badge>}
            {failed.map((f: any) => (
              <Badge key={f.code} tone="err" title={f.message}>
                {f.title}
              </Badge>
            ))}
            {unknown.length > 0 && (
              <Badge
                tone="warn"
                title={unknown.map((f: any) => `${f.title}: ${f.message}`).join("\n")}
              >
                не определено: {unknown.length}
              </Badge>
            )}
          </div>
        </div>
        <div>
          <div className="muted">Признаки генеративного ИИ</div>
          <Badge tone={(ai.score ?? 0) >= 0.5 ? "warn" : "ok"}>
            {ai.confidence ?? "—"} ({(ai.score ?? 0).toFixed(2)})
          </Badge>
          <div className="muted mt-0.5">на балл не влияет — только порядок проверки</div>
        </div>
      </div>

      {review.reviewer_summary && (
        <p className="mt-3">
          <span className="muted">Вывод для ревьюера: </span>
          {review.reviewer_summary}
        </p>
      )}
      <p className="muted mt-2">
        Полный разбор с цитатами по блокам документа — на вкладке ревьюера,
        которому назначена работа. Балл становится оценкой только после его
        подтверждения.
      </p>
    </div>
  );
}

/** Схожесть работ: дублирование и заимствования. */
function SimilarityPanel({ assignmentId, toast }: { assignmentId: string; toast: Toast }) {
  const [data, setData] = useState<any>(null);
  const [busy, setBusy] = useState(false);

  return (
    <Section
      title="Схожесть работ"
      hint={
        <>
          Ищет совпадающие куски текста между работами одного задания:
          TF-IDF по символьным n-граммам плюс пересечение шинглов, без
          моделей и без сети. Работы по одному заданию неизбежно похожи,
          поэтому порог намеренно высокий, а вывод — повод посмотреть
          глазами, а не обвинение.
        </>
      }
      defaultOpen={false}
      right={
        <button
          className="btn"
          disabled={busy}
          onClick={() => {
            setBusy(true);
            api
              .similarity(assignmentId)
              .then(setData)
              .catch((e) => toast(String(e.message ?? e), "err"))
              .finally(() => setBusy(false));
          }}
        >
          {busy ? "Сравнение…" : "Сравнить"}
        </button>
      }
    >
      {!data && <Empty>Нажмите «Сравнить».</Empty>}
      {data && (
        <>
          {data.notes?.map((n: string, i: number) => (
            <div key={i} className="muted mb-2 text-xs">
              {n}
            </div>
          ))}
          <table className="w-full text-xs">
            <tbody>
              {data.pairs.map((p: any, i: number) => (
                <tr key={i} className="border-b last:border-0" style={{ borderColor: "var(--border)" }}>
                  <td className="py-1">{p.a_name}</td>
                  <td className="py-1">{p.b_name}</td>
                  <td className="w-32 py-1">
                    <Bar value={p.score} max={1} tone={p.suspicious ? "err" : "ok"} />
                  </td>
                  <td className="py-1 text-right tabular-nums">{(p.score * 100).toFixed(0)}%</td>
                  <td className="py-1">
                    {p.suspicious && <Badge tone="err">требует внимания</Badge>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </Section>
  );
}
