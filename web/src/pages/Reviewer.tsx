/**
 * Вкладка ревьюера — главный экран продукта.
 *
 * Слева очередь, отсортированная по индексу приоритета ручной проверки.
 * Справа карточка: формальные нарушения, баллы по критериям с проверенными
 * цитатами, сигнал генеративного ИИ с основаниями и ограничениями,
 * просмотрщик документа с подсветкой и подтверждение результата.
 *
 * Сквозная мысль экрана: решение принимает человек. Балл предварительный,
 * цитаты помечены как подтверждённые или нет, сигнал ИИ можно отклонить,
 * итог публикуется студенту только после нажатия «Подтвердить».
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { api, download, type User } from "../api";
import {
  ActionButton,
  Badge,
  Bar,
  Empty,
  Hint,
  Section,
  Spinner,
  StatusBadge,
  StatusIcon,
} from "../components/ui";
import { RubricPanel } from "./Coordinator";
import { CreateAssignment } from "./Manage";

type Toast = (t: string, tone?: "info" | "ok" | "warn" | "err") => void;

export default function Reviewer({
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
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [refs, setRefs] = useState<any>(null);

  useEffect(() => {
    api.tracks().then(setRefs).catch(() => setRefs(null));
  }, []);

  useEffect(() => {
    api.submissions().then((rows) => {
      setItems(rows);
      // Открывается первая работа, в которой есть что смотреть. Занятые
      // места прошлого потока лежат в конце очереди, но если выбирать
      // просто «первую строку», экран мог открыться на строке без файла.
      setSelected(
        (cur) =>
          cur ?? rows.find((r: any) => r.has_file !== false)?.id ?? rows[0]?.id ?? null,
      );
    });
  }, [tick, user.id]);

  const load = useCallback(() => {
    if (!selected) return;
    setLoading(true);
    api
      .submission(selected)
      .then(setDetail)
      .catch((e) => toast(String(e.message ?? e), "err"))
      .finally(() => setLoading(false));
  }, [selected, toast]);

  useEffect(load, [load, tick]);

  return (
    /* min-w-0 на детях сетки обязателен: по умолчанию элемент сетки не
       сжимается уже своего min-content, а имя файла «Product_Fraud_ДЗ2_…»
       — один неразрывный токен. Из-за него колонка раздувалась до 404 px
       в контейнере 343 px, страница уезжала вбок, а класс truncate не
       срабатывал вовсе. */
    <div className="grid gap-4 lg:grid-cols-[340px_1fr]">
      <aside className="flex min-w-0 flex-col gap-2">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <div className="flex items-center gap-2">
            <h2 className="font-semibold">Моя очередь</h2>
            {/*
              Два абзаца пояснения висели здесь постоянно и отодвигали саму
              очередь вниз. Читают их один раз — значит, это подсказка.
            */}
            <Hint
              text={
                <>
                  Сверху те работы, где автоматика вероятнее ошиблась: слабые
                  доказательства, признаки ИИ, балл на границе. Смотрите по
                  порядку. Проверку моделью запускаете вы — кнопкой в карточке
                  работы справа; методист может запустить сразу по всему
                  заданию. Балл остаётся предварительным, пока вы его не
                  подтвердите.
                </>
              }
            />
          </div>
          <span className="muted text-xs">{items?.length ?? 0} работ</span>
        </div>
        {/*
          Ревьюер заводит типовое ДЗ сам: он ближе к предмету, чем
          координатор потока. Заведённое здесь задание появляется у
          студента в списке при сдаче.
        */}
        <CreateAssignment refs={refs} toast={toast} onCreated={() => refresh()} />
        {!items && <Spinner label="Загрузка…" />}
        {items?.length === 0 && <Empty>Вам пока не назначено работ.</Empty>}
        {/*
          Очередь только из занятых мест — это не «пусто», но и работать
          не с чем. Молчать здесь нельзя: экран выглядит рабочим, а кнопки
          проверки нет ни в одной карточке, и причина не видна.
        */}
        {items != null && items.length > 0 && items.every((s) => s.has_file === false) && (
          <div
            className="rounded p-2 text-xs"
            style={{ background: "var(--surface-2)", color: "var(--warn)" }}
          >
            Проверять пока нечего: все строки в вашей очереди — занятые места
            прошлого потока без файлов. Работы появятся, когда методист
            распределит новые сдачи.
          </div>
        )}
        {items?.map((s) => (
          <button
            key={s.id}
            onClick={() => setSelected(s.id)}
            className="card text-left"
            style={{
              borderColor: s.id === selected ? "var(--brand)" : "var(--border)",
              borderWidth: s.id === selected ? 2 : 1,
            }}
          >
            {/*
              Строка без файла — это не сдача, а занятое место в очереди
              ревьюера. Раньше она рисовалась как обычная работа, и рядом
              оказывались взаимоисключающие подписи: «без файла» и «сдано
              в срок». Срок у того, чего не сдавали, смысла не имеет —
              как и имя студента, приоритет и балл.
            */}
            {s.has_file === false ? (
              <div style={{ opacity: 0.6 }}>
                <div className="text-sm font-medium">Место занято</div>
                <div className="muted truncate text-xs" title={s.file_name}>
                  {s.file_name}
                </div>
                <div className="mt-1.5">
                  <Badge title="Работа числится за вами, но файла у неё нет">
                    файла нет — проверять нечего
                  </Badge>
                </div>
              </div>
            ) : (
              <>
                <div className="flex items-start justify-between gap-2">
                  <span className="text-sm font-medium">{s.student_name}</span>
                  <PriorityDot value={s.priority_index ?? 0} />
                </div>
                <div className="muted truncate text-xs" title={s.file_name}>
                  {s.file_name}
                </div>
                <div className="mt-1.5 flex flex-wrap items-center gap-1">
                  <StatusBadge status={s.status} />
                  <DeadlineBadge state={s.deadline_state} label={s.deadline_label} />
                  {s.late_penalty > 0 && <Badge tone="err">штраф −{s.late_penalty}</Badge>}
                </div>
              </>
            )}
            {s.has_file !== false && s.preliminary_score != null && (
              <div className="mt-2">
                <div className="mb-0.5 flex justify-between text-xs">
                  <span className="muted">предварительно</span>
                  <span className="font-semibold tabular-nums">
                    {s.preliminary_score} / {s.max_score}
                  </span>
                </div>
                <Bar value={s.preliminary_score} max={s.max_score || 1} />
              </div>
            )}
            {s.failed_steps?.length > 0 && (
              <div className="mt-1">
                <Badge tone="warn">не удалось: {s.failed_steps.join(", ")}</Badge>
              </div>
            )}
          </button>
        ))}
      </aside>

      <section className="min-w-0">
        {!selected && <Empty>Выберите работу из очереди.</Empty>}
        {selected && loading && !detail && <Spinner label="Загрузка работы…" />}
        {selected && detail && (
          <SubmissionCard detail={detail} toast={toast} onChanged={refresh} reload={load} />
        )}
      </section>
    </div>
  );
}

function PriorityDot({ value }: { value: number }) {
  const tone = value >= 0.5 ? "err" : value >= 0.25 ? "warn" : "ok";
  const label = value >= 0.5 ? "высокий" : value >= 0.25 ? "средний" : "низкий";
  return (
    <Badge tone={tone} title={`Насколько нужна ручная проверка: ${value.toFixed(2)} из 1`}>
      {label} {value.toFixed(2)}
    </Badge>
  );
}

function DeadlineBadge({ state, label }: { state: string; label: string }) {
  const tone =
    state === "late_zero" ? "err" : state === "late_penalty" ? "warn" : state === "due_soon" ? "warn" : "ok";
  return <Badge tone={tone}>{label}</Badge>;
}

function SubmissionCard({
  detail,
  toast,
  onChanged,
  reload,
}: {
  detail: any;
  toast: Toast;
  onChanged: () => void;
  reload: () => void;
}) {
  const review = detail.review;
  const [scores, setScores] = useState<Record<string, number>>({});
  const [feedback, setFeedback] = useState("");
  const [highlight, setHighlight] = useState<{ blocks: number[]; kind: string } | null>(null);
  // ПДн-слой выключен по умолчанию: он показывает исходные значения, и
  // включать его нужно осознанно, а не видеть при каждом открытии работы.
  const [layers, setLayers] = useState({
    evidence: true, ai: true, formal: true, pii: false,
  });

  /*
    Черновик ревьюера защищён от фоновых обновлений.

    Живая шина событий дёргает перезагрузку карточки на каждое событие в
    системе: завершилась чужая проверка, методист сдвинул сроки другого ДЗ,
    прошло распределение. Каждый ответ GET приходит новым объектом, и форма
    переинициализировалась — набранный, но не отправленный комментарий
    молча заменялся сохранённым. Ревьюер терял работу, ничего не нажимая.

    Поэтому форма заполняется по идентификатору работы, а не по объекту
    ответа. Новый результат по этой же работе (у него другое `created_at`)
    не подменяет ввод: если ревьюер уже что-то правил, появляется
    предложение принять обновление явно.
  */
  const [base, setBase] = useState<{ id: string; stamp: string } | null>(null);
  const [dirty, setDirty] = useState(false);
  const [incoming, setIncoming] = useState<string | null>(null);

  const stamp: string = review?.created_at ?? "";

  const adopt = useCallback(() => {
    const init: Record<string, number> = {};
    for (const c of review?.criteria ?? []) init[c.criterion_id] = c.score;
    setScores(init);
    setFeedback(
      detail.final_score != null ? detail.feedback ?? "" : review?.student_feedback ?? "",
    );
    setHighlight(null);
    setBase({ id: detail.id, stamp });
    setDirty(false);
    setIncoming(null);
  }, [detail.id, detail.final_score, detail.feedback, review, stamp]);

  useEffect(() => {
    // Другая работа — форма заполняется заново, это ожидаемо.
    if (base == null || base.id !== detail.id) {
      adopt();
      return;
    }
    // Та же работа, но результат обновился.
    if (base.stamp !== stamp) {
      if (dirty) setIncoming(stamp);
      else adopt();
    }
  }, [detail.id, stamp, base, dirty, adopt]);

  /** Правка формы — с этого момента черновик защищён. */
  const editScores = (next: Record<string, number>) => {
    setScores(next);
    setDirty(true);
  };
  const editFeedback = (next: string) => {
    setFeedback(next);
    setDirty(true);
  };

  // Жёсткий срок пройден — по правилу из условия работа оценивается в ноль,
  // сколько бы баллов ни набрали критерии. Это решает сервер; интерфейс
  // обязан показывать то же самое, иначе ревьюер увидит одно число, а
  // студент получит другое.
  const forcedZero = detail.deadline_state === "late_zero";

  const total = useMemo(() => {
    if (forcedZero) return 0;
    const sum = Object.values(scores).reduce((a, b) => a + b, 0);
    const penalties = (review?.penalties ?? []).reduce(
      (a: number, p: any) => a + (p.amount ?? 0),
      0,
    );
    return Math.max(0, sum - penalties);
  }, [scores, review, forcedZero]);

  if (!review) {
    return (
      <div className="card">
        <div className="mb-2 font-semibold">{detail.file_name}</div>
        {detail.has_file === false ? (
          /*
            Строка без файла. Такие остались от прошлого потока: они заняты
            у ревьюера и нужны, чтобы распределение выравнивало реальный
            перекос нагрузки. Раньше здесь предлагалась кнопка «Запустить»,
            и нажатие давало отказ «Формат '' не поддерживается» — сообщение,
            по которому невозможно понять, что файла просто нет.
          */
          <Empty>
            К этой работе не приложен файл — проверять нечего.
            <div className="muted mx-auto mt-2 max-w-md text-xs">
              Так выглядят строки нагрузки прошлого потока: они занимают место
              в вашей очереди, но содержимого у них нет.
            </div>
          </Empty>
        ) : detail.rubric_approved === false ? (
          <div>
            <Empty>
              Критерии оценивания ещё не утверждены — проверка не запустится.
              <div className="muted mx-auto mt-2 max-w-md text-xs">
                Задать критерии можно прямо здесь: загрузите файл условия или
                напишите требования текстом. Утверждает их методист.
              </div>
            </Empty>
            <RubricPanel
              assignment={{
                id: detail.assignment_id,
                rubric: detail.rubric,
                rubric_approved: false,
              }}
              toast={toast}
              onChanged={onChanged}
              canApprove={false}
            />
          </div>
        ) : (
          <Empty>
            Предварительная проверка ещё не выполнена.
            <div className="mt-3">
              <ActionButton
                primary
                doneLabel="В очереди"
                onAction={() =>
                  api
                    .startReview(detail.id)
                    .then(() => toast("Работа поставлена в очередь", "ok"))
                    .then(onChanged)
                    .catch((e) => {
                      toast(String(e.message ?? e), "err");
                      throw e;
                    })
                }
              >
                Запустить проверку
              </ActionButton>
            </div>
          </Empty>
        )}
      </div>
    );
  }

  // Возвращает промис: кнопка сама показывает «идёт отправка» и «готово»,
  // а раньше после нажатия не менялось ничего и было непонятно, ушло ли.
  const confirm = () =>
    api
      .confirm(detail.id, {
        final_score: total,
        feedback,
        criteria_scores: scores,
      })
      .then((r) => {
        toast(
          `Результат отправлен студенту. Балл ${r.final_score}` +
            (r.score_edited ? " — вы его изменили." : " — без правок."),
          "ok",
        );
        // Отправленное становится новой основой черновика: иначе следующее
        // фоновое событие сочло бы форму «с несохранёнными правками» и
        // спрашивало бы про обновление на пустом месте.
        setDirty(false);
        setIncoming(null);
        onChanged();
        reload();
      })
      .catch((e) => {
        toast(String(e.message ?? e), "err");
        throw e;
      });

  return (
    <div className="flex flex-col gap-3">
      <div className="card">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="font-semibold">{detail.file_name}</div>
            <div className="muted text-xs">
              {detail.student_name} · сдано{" "}
              {new Date(detail.submitted_at).toLocaleString("ru-RU")} ·{" "}
              {detail.track_name ?? detail.track}
            </div>
            <div className="mt-1 text-xs">{detail.deadline_detail}</div>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              {/*
                Ссылка на исходный файл. Текстовая модель не смотрит
                изображения и честно пишет «изображение не оценено», но
                посмотреть схему или матрицу глазами было негде: файл лежал
                на диске без единой ссылки из интерфейса.
              */}
              <button
                className="btn text-xs"
                onClick={() =>
                  download(api.originalFileUrl(detail.id), detail.file_name).catch((e) =>
                    toast(String(e.message ?? e), "err"),
                  )
                }
                title="Скачать файл, который сдал студент — посмотреть схемы и изображения глазами"
              >
                Открыть оригинал
              </button>
              {detail.rubric_stale && (
                <Badge
                  tone="warn"
                  title="Критерии оценивания изменились после этой проверки. Баллы посчитаны по прежней рубрике — запустите проверку заново."
                >
                  проверено по прежним критериям
                </Badge>
              )}
            </div>
          </div>
          <div className="text-right">
            <div className="muted text-xs">предварительный балл</div>
            <div className="text-2xl font-semibold tabular-nums">
              {review.preliminary_score} <span className="muted text-base">/ {review.max_score}</span>
            </div>
            {review.penalties?.map((p: any) => (
              <div key={p.code} className="text-xs" style={{ color: "var(--err)" }}>
                −{p.amount} {p.title}
              </div>
            ))}
            {/*
              Кнопка запуска модели была видна только у работ, где проверки
              ещё нет: увидеть её можно было случайно, а найти — никак.
              Теперь она в шапке карточки всегда, и рядом сказано, что
              именно она делает.
            */}
            <div className="mt-2">
              <ActionButton
                doneLabel="В очереди"
                className="text-xs"
                title="Перезапустить проверку моделью: заново прочитать работу и пересчитать баллы по критериям"
                onAction={() =>
                  api
                    .startReview(detail.id)
                    .then(() => {
                      toast(
                        "Проверка запущена. Обычно занимает от 45 секунд — "
                        + "результат придёт уведомлением.",
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
                Проверить заново моделью
              </ActionButton>
            </div>
          </div>
        </div>

        {/*
          Пришёл новый результат проверки, а в форме есть несохранённые
          правки. Раньше он просто затирал их: любое фоновое событие —
          чужая проверка, сдвиг сроков другого ДЗ — перезагружало карточку
          и подменяло набранный комментарий сохранённым.
        */}
        {incoming && (
          <div
            className="mt-3 flex flex-wrap items-center gap-3 rounded p-2 text-xs"
            style={{ background: "var(--surface-2)", color: "var(--warn)" }}
          >
            <span>
              Пришёл новый результат проверки этой работы. Ваши правки не
              тронуты — примите обновление, когда будете готовы.
            </span>
            <button className="btn text-xs" onClick={adopt}>
              Принять новый результат
            </button>
            <button className="btn text-xs" onClick={() => setIncoming(null)}>
              Оставить свои правки
            </button>
          </div>
        )}

        {review.priority_reasons?.length > 0 && (
          <div className="mt-3 rounded p-2 text-xs" style={{ background: "var(--surface-2)" }}>
            <span className="font-medium">
              Почему эта работа наверху очереди ({review.priority_index?.toFixed(2)} из 1):
            </span>{" "}
            {review.priority_reasons.join("; ")}
            <div className="muted mt-1">
              Это только порядок просмотра. На оценку не влияет: балл и очередь
              считаются разными формулами.
            </div>
          </div>
        )}

        {review.warnings?.length > 0 && (
          <ul className="mt-2 text-xs" style={{ color: "var(--warn)" }}>
            {review.warnings.map((w: string, i: number) => (
              <li key={i}>! {w}</li>
            ))}
          </ul>
        )}
      </div>

      {detail.previous && (
        <PreviousVersion previous={detail.previous} review={review} version={detail.version} />
      )}

      <FormalSection
        formal={review.formal ?? []}
        onHighlight={(blocks) => setHighlight({ blocks, kind: "gap" })}
      />

      {/*
        Пока критерии не утверждены, проверка не запускается, и ревьюер
        упирается в тупик: экран есть, кнопка есть, а результата не будет.
        Поэтому панель критериев показывается и здесь — ревьюер может
        загрузить условие или написать требования текстом. Утверждает
        по-прежнему методист: это его зона ответственности.
      */}
      {detail.rubric_approved === false && (
        <RubricPanel
          assignment={{
            id: detail.assignment_id,
            rubric: detail.rubric,
            rubric_approved: false,
          }}
          toast={toast}
          onChanged={reload}
          canApprove={false}
        />
      )}

      <CriteriaSection
        criteria={review.criteria ?? []}
        rubric={detail.rubric}
        scores={scores}
        setScores={editScores}
        onHighlight={(blocks) => setHighlight({ blocks, kind: "evidence" })}
      />

      {review.ai_signal && (
        <AISignalSection
          signal={review.ai_signal}
          verdict={detail.ai_verdict}
          onVerdict={(v, c) =>
            api
              .aiVerdict(detail.id, v, c)
              .then(() => {
                toast(v === "confirmed" ? "Сигнал подтверждён" : "Сигнал отклонён", "ok");
                reload();
              })
              .catch((e) => toast(String(e.message ?? e), "err"))
          }
        />
      )}

      <Section
        title="Документ с подсветкой"
        defaultOpen={false}
        right={
          <div className="flex flex-wrap justify-end gap-x-2 gap-y-1 text-xs">
            {(
              [
                ["evidence", "доказательства", "var(--ok)"],
                ["ai", "признаки ИИ", "#a371f7"],
                ["formal", "нарушения", "var(--warn)"],
                ["pii", "персональные данные", "var(--text-dim)"],
              ] as const
            ).map(([key, label, color]) => (
              <label key={key} className="flex cursor-pointer items-center gap-1">
                <input
                  type="checkbox"
                  checked={(layers as any)[key]}
                  onChange={(e) => setLayers({ ...layers, [key]: e.target.checked })}
                />
                <span style={{ color }}>{label}</span>
              </label>
            ))}
          </div>
        }
      >
        <DocumentViewer
          blocks={detail.blocks ?? []}
          review={review}
          layers={layers}
          highlight={highlight}
        />
      </Section>

      <Section
        title="Как шла проверка: шаги и время"
        hint="Служебный журнал: какие шаги отработали, какие отказали и сколько заняли. Нужен, чтобы понять, почему чего-то нет в карточке."
        defaultOpen={false}
      >
        <table className="w-full text-xs">
          <tbody>
            {(review.trace ?? []).map((t: any, i: number) => (
              <tr key={i} className="border-b last:border-0" style={{ borderColor: "var(--border)" }}>
                <td className="py-1 pr-2">
                  <span style={{ color: t.ok ? "var(--ok)" : "var(--err)" }}>
                    {t.ok ? "✔" : "✘"}
                  </span>
                </td>
                <td className="py-1 pr-2">{t.name}</td>
                <td className="py-1 pr-2 text-right tabular-nums muted">
                  {Math.round(t.duration_ms)} мс
                </td>
                <td className="py-1 muted">{t.detail}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="muted mt-2 text-xs">
          Всего {(review.duration_ms / 1000).toFixed(1)} с, модель {review.model}.
        </div>
      </Section>

      <div className="card">
        <div className="mb-2 font-semibold">Итоговое решение</div>
        <p className="muted mb-3 text-xs">
          Балл предварительный. Студент увидит результат только после вашего подтверждения.
        </p>
        <label className="mb-1 block text-xs muted">Обратная связь студенту</label>
        <textarea
          className="input mb-3 min-h-[160px]"
          value={feedback}
          onChange={(e) => editFeedback(e.target.value)}
        />
        <div className="flex flex-wrap items-center gap-3">
          <div className="text-sm">
            Итоговый балл: <span className="text-xl font-semibold tabular-nums">{total.toFixed(1)}</span>
            <span className="muted"> / {review.max_score}</span>
            {Math.abs(total - review.preliminary_score) > 0.001 && (
              <span className="ml-2" style={{ color: "var(--warn)" }}>
                изменён (было {review.preliminary_score})
              </span>
            )}
            {forcedZero && (
              <div className="mt-1 text-xs" style={{ color: "var(--err)" }}>
                Жёсткий срок сдачи пройден: по правилу из условия работа
                оценивается в ноль баллов независимо от содержания. Баллы по
                критериям сохраняются в разборе — студент увидит, что именно
                было сделано.
              </div>
            )}
          </div>
          <div className="ml-auto flex gap-2">
            {([["md", "Markdown"], ["html", "HTML"], ["json", "JSON"]] as const).map(
              ([fmt, label]) => (
                <ActionButton
                  key={fmt}
                  doneLabel="Скачано"
                  title={
                    fmt === "json"
                      ? "Полный результат проверки со всеми внутренними полями"
                      : `Скачать разбор в ${label}`
                  }
                  onAction={() =>
                    download(
                      api.exportReviewUrl(detail.id, fmt),
                      `review_${detail.id.slice(0, 8)}.${fmt}`,
                    ).catch((e) => {
                      toast(String(e.message ?? e), "err");
                      throw e;
                    })
                  }
                >
                  {label}
                </ActionButton>
              ),
            )}
            <ActionButton
              primary
              onAction={confirm}
              doneLabel={detail.confirmed_at ? "Обновлено" : "Отправлено студенту"}
              confirm={
                detail.confirmed_at
                  ? undefined
                  : `Отправить студенту балл ${total.toFixed(1)} из ${review.max_score}? ` +
                    `После этого он увидит оценку и вашу обратную связь.`
              }
            >
              {detail.confirmed_at ? "Обновить результат" : "Подтвердить и отправить"}
            </ActionButton>
          </div>
        </div>
        {detail.confirmed_at ? (
          <div className="mt-2 text-xs" style={{ color: "var(--ok)" }}>
            ✓ Результат отправлен студенту{" "}
            {new Date(detail.confirmed_at).toLocaleString("ru-RU")}. Студент видит
            балл и обратную связь. Правки нужно отправить заново.
          </div>
        ) : (
          <div className="muted mt-2 text-xs">
            Пока не отправлено: студент результата не видит.
          </div>
        )}
      </div>
    </div>
  );
}

function FormalSection({
  formal,
  onHighlight,
}: {
  formal: any[];
  onHighlight: (blocks: number[]) => void;
}) {
  const violations = formal.filter((f) => f.status === "fail").length;
  const unknown = formal.filter((f) => f.status === "unknown").length;
  return (
    <Section
      title={
        <span>
          Формальные проверки{" "}
          <span className="muted text-xs font-normal">
            (без модели · нарушений {violations}, не определено {unknown})
          </span>
        </span>
      }
    >
      <div className="flex flex-col gap-1.5">
        {formal.map((f) => (
          <div
            key={f.code}
            className="flex items-start gap-2 rounded px-2 py-1.5 text-sm"
            style={{ background: f.status === "fail" ? "var(--surface-2)" : "transparent" }}
          >
            <StatusIcon status={f.status} />
            <div className="flex-1">
              <div className="font-medium">{f.title}</div>
              <div className="muted text-xs">{f.message}</div>
              {f.evidence?.length > 0 && (
                <div className="muted mt-0.5 text-[11px]">{f.evidence.join(" · ")}</div>
              )}
              {f.requirement && (
                <div className="mt-0.5 text-[11px]" style={{ color: "var(--info)" }}>
                  требование: {f.requirement}
                </div>
              )}
            </div>
            {f.block_refs?.length > 0 && (
              <button className="btn text-xs" onClick={() => onHighlight(f.block_refs)}>
                показать
              </button>
            )}
          </div>
        ))}
      </div>
    </Section>
  );
}

function CriteriaSection({
  criteria,
  rubric,
  scores,
  setScores,
  onHighlight,
}: {
  criteria: any[];
  rubric: any;
  scores: Record<string, number>;
  setScores: (s: Record<string, number>) => void;
  onHighlight: (blocks: number[]) => void;
}) {
  // Требование по критерию — то, с чем ревьюер сверяет работу. Раньше его
  // на этом экране не было вовсе: критерий назывался «Качество оформления»,
  // а что именно требуется, приходилось искать у методиста.
  const requirementOf = (id: string): string =>
    (rubric?.criteria ?? []).find((c: any) => c.id === id)?.requirements ?? "";
  return (
    <Section
      title="Оценка по критериям"
      hint="Предварительный балл по каждому критерию с цитатами из работы. Цитаты проверены кодом: помеченные ✓ действительно найдены в тексте. Балл можно менять."
    >
      <div className="flex flex-col gap-4">
        {criteria.map((c) => (
          <div key={c.criterion_id} className="border-b pb-4 last:border-0" style={{ borderColor: "var(--border)" }}>
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{c.criterion_name}</span>
              {c.failed && <Badge tone="err">не оценён</Badge>}
              {c.unverified && (
                <Badge tone="warn" title="Ни одна цитата не найдена в работе">
                  цитаты не подтверждены
                </Badge>
              )}
              <div className="ml-auto flex items-center gap-2">
                <input
                  type="number"
                  className="input w-20 text-right tabular-nums"
                  step={0.5}
                  min={0}
                  max={c.max_score}
                  value={scores[c.criterion_id] ?? c.score}
                  onChange={(e) =>
                    setScores({
                      ...scores,
                      [c.criterion_id]: Math.max(
                        0,
                        Math.min(c.max_score, Number(e.target.value) || 0),
                      ),
                    })
                  }
                />
                <span className="muted text-sm">/ {c.max_score}</span>
              </div>
            </div>

            {requirementOf(c.criterion_id) && (
              <details className="mt-1">
                <summary className="muted cursor-pointer text-xs">
                  что требуется по этому критерию
                </summary>
                <div className="muted mt-1 whitespace-pre-wrap text-xs">
                  {requirementOf(c.criterion_id)}
                </div>
              </details>
            )}

            {c.failed ? (
              <div className="mt-1 text-xs" style={{ color: "var(--err)" }}>
                {c.error} — оцените вручную.
              </div>
            ) : (
              <>
                <p className="mt-1 text-sm">{c.verdict}</p>

                {c.evidence?.length > 0 && (
                  <div className="mt-2">
                    <div className="muted mb-1 text-xs">
                      Доказательства ({c.evidence_verified} из {c.evidence_total} подтверждены кодом)
                    </div>
                    <div className="flex flex-col gap-1">
                      {c.evidence.map((ev: any, i: number) => (
                        <button
                          key={i}
                          className="flex items-start gap-2 rounded px-2 py-1 text-left text-xs"
                          style={{ background: "var(--surface-2)" }}
                          onClick={() => onHighlight([ev.found_in_block ?? ev.block])}
                          title={evidenceHint(ev)}
                        >
                          <span
                            style={{
                              color:
                                ev.status === "verified"
                                  ? "var(--ok)"
                                  : ev.status === "wrong_block" || ev.status === "approximate"
                                    ? "var(--warn)"
                                    : "var(--err)",
                            }}
                          >
                            {/* Зелёная галочка — только за дословное вхождение.
                                Нечёткое совпадение помечается «≈»: цитата с
                                отброшенной частицей «не» набирала 94 % и
                                выглядела подтверждённой. */}
                            {ev.status === "verified"
                              ? "✔"
                              : ev.status === "wrong_block"
                                ? "~"
                                : ev.status === "approximate"
                                  ? "≈"
                                  : "✘"}
                          </span>
                          <span className="muted whitespace-nowrap">§{ev.block}</span>
                          <span className="flex-1">«{ev.quote}»</span>
                          <span className="muted tabular-nums whitespace-nowrap">
                            {ev.similarity}%
                          </span>
                        </button>
                      ))}
                    </div>
                  </div>
                )}

                {c.evidence_total === 0 && (
                  <div className="mt-2 text-xs" style={{ color: "var(--warn)" }}>
                    Модель не привела ни одной цитаты — проверьте критерий вручную.
                  </div>
                )}

                {c.gaps?.length > 0 && (
                  <div className="mt-2">
                    <div className="muted mb-1 text-xs">Чего не хватает</div>
                    <ul className="ml-4 list-disc text-xs">
                      {c.gaps.map((g: string, i: number) => (
                        <li key={i}>{g}</li>
                      ))}
                    </ul>
                  </div>
                )}

                {c.recommendation && (
                  <div className="mt-2 text-xs" style={{ color: "var(--info)" }}>
                    На что посмотреть: {c.recommendation}
                  </div>
                )}
              </>
            )}
          </div>
        ))}
      </div>
    </Section>
  );
}

function evidenceHint(ev: any): string {
  switch (ev.status) {
    case "verified":
      return `Цитата дословно есть в §${ev.found_in_block}`;
    case "approximate":
      return (
        `Дословно такого текста в работе нет. Ближайший фрагмент — в ` +
        `§${ev.found_in_block}, схожесть ${ev.similarity}%. Отличаться может ` +
        `частица «не» или число, поэтому подтверждением это не считается: ` +
        `сверьте цитату глазами.`
      );
    case "wrong_block":
      return `Текст найден дословно, но в §${ev.found_in_block}, а не в §${ev.block}`;
    case "no_block":
      return `Блока §${ev.block} в работе нет`;
    default:
      return `В работе не найдено (лучшая схожесть ${ev.similarity}%)`;
  }
}

function AISignalSection({
  signal,
  verdict,
  onVerdict,
}: {
  signal: any;
  verdict: string | null;
  onVerdict: (v: string, comment: string) => void;
}) {
  const [comment, setComment] = useState("");
  const tone = signal.score >= 0.6 ? "err" : signal.score >= 0.35 ? "warn" : "ok";

  return (
    <Section
      title="Признаки генеративного ИИ"
      right={
        <Badge tone={tone}>
          {signal.score.toFixed(2)} · уверенность: {signal.confidence}
        </Badge>
      }
    >
      <div
        className="mb-3 rounded p-2 text-xs"
        style={{ background: "var(--surface-2)", color: "var(--text-dim)" }}
      >
        Сигнал носит рекомендательный характер и <b>не является доказательством
        нарушения</b>. На балл он не влияет — только на приоритет ручной проверки.
        Подтвердить или отклонить его может только ревьюер.
      </div>

      <table className="mb-3 w-full text-xs">
        <tbody>
          {(signal.signals ?? []).map((s: any) => (
            <tr key={s.code} className="border-b last:border-0" style={{ borderColor: "var(--border)" }}>
              <td className="py-1 pr-2 font-medium">{s.title}</td>
              <td className="py-1 pr-2 text-right tabular-nums">
                {s.available && s.value != null ? (
                  s.value.toFixed(2)
                ) : (
                  <span className="muted">недоступен</span>
                )}
              </td>
              <td className="py-1 muted">{s.detail}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="grid gap-3 md:grid-cols-2">
        <div>
          <div className="mb-1 text-xs font-medium">Основания</div>
          <ul className="ml-4 list-disc text-xs">
            {(signal.grounds ?? []).map((g: string, i: number) => (
              <li key={i}>{g}</li>
            ))}
          </ul>
        </div>
        <div>
          <div className="mb-1 text-xs font-medium">Ограничения метода</div>
          <ul className="ml-4 list-disc text-xs muted">
            {(signal.limitations ?? []).map((l: string, i: number) => (
              <li key={i}>{l}</li>
            ))}
          </ul>
        </div>
      </div>

      <div className="mt-3 text-xs">
        Декларация студента об использовании ИИ:{" "}
        {signal.declaration_found ? (
          <Badge tone="ok">найдена в работе</Badge>
        ) : (
          <Badge tone="default">не найдена</Badge>
        )}
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <input
          className="input flex-1"
          placeholder="Комментарий к решению (необязательно)"
          value={comment}
          onChange={(e) => setComment(e.target.value)}
        />
        <button className="btn" onClick={() => onVerdict("rejected", comment)}>
          Отклонить сигнал
        </button>
        <button className="btn" onClick={() => onVerdict("confirmed", comment)}>
          Подтвердить сигнал
        </button>
      </div>
      {verdict && (
        <div className="mt-2 text-xs">
          Решение ревьюера:{" "}
          <Badge tone={verdict === "confirmed" ? "err" : "ok"}>
            {verdict === "confirmed" ? "признаки подтверждены" : "сигнал отклонён"}
          </Badge>
        </div>
      )}
    </Section>
  );
}

/**
 * Повторная сдача: что изменилось с прошлой версии.
 *
 * Сравнение идёт по критериям, а не по одному итоговому баллу: студент мог
 * доработать ровно то, о чём его просили, и общий балл при этом почти не
 * сдвинуться. Ревьюеру нужно видеть именно распределение изменений — иначе
 * повторная проверка превращается в чтение работы заново.
 */
function PreviousVersion({
  previous,
  review,
  version,
}: {
  previous: any;
  review: any;
  version: number;
}) {
  const now = new Map<string, any>(
    (review?.criteria ?? []).map((c: any) => [c.criterion_id, c]),
  );
  const before: Record<string, number> = previous.per_criterion ?? {};
  const prevScore = previous.final_score ?? previous.preliminary_score;
  const delta =
    prevScore != null && review?.preliminary_score != null
      ? review.preliminary_score - prevScore
      : null;

  return (
    <Section
      title={`Версия ${version}: сравнение с предыдущей`}
      right={
        delta != null ? (
          <Badge tone={delta > 0 ? "ok" : delta < 0 ? "warn" : undefined}>
            {delta > 0 ? "+" : ""}
            {delta.toFixed(1)} балла к версии {previous.version}
          </Badge>
        ) : undefined
      }
    >
      <p className="muted mb-2 text-xs">
        Предыдущая версия «{previous.file_name}» сдана{" "}
        {new Date(previous.submitted_at).toLocaleString("ru-RU")}, балл{" "}
        {prevScore ?? "—"}. Она сохранена целиком — не перезаписана.
      </p>

      <table className="w-full text-xs">
        <thead className="muted text-left">
          <tr>
            <th className="py-1">критерий</th>
            <th className="py-1 text-right">было</th>
            <th className="py-1 text-right">стало</th>
            <th className="py-1 text-right">изменение</th>
          </tr>
        </thead>
        <tbody>
          {[...now.values()].map((c: any) => {
            const was = before[c.criterion_id];
            const d = was != null ? c.score - was : null;
            return (
              <tr key={c.criterion_id} className="border-b last:border-0" style={{ borderColor: "var(--border)" }}>
                <td className="py-1">{c.criterion_name}</td>
                <td className="py-1 text-right tabular-nums">{was ?? "—"}</td>
                <td className="py-1 text-right tabular-nums">{c.score}</td>
                <td
                  className="py-1 text-right tabular-nums"
                  style={{
                    color: d == null ? undefined : d > 0 ? "var(--ok)" : d < 0 ? "var(--err)" : undefined,
                  }}
                >
                  {d == null ? "—" : d === 0 ? "0" : `${d > 0 ? "+" : ""}${d.toFixed(1)}`}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      {previous.feedback && (
        <details className="mt-2 text-xs">
          <summary className="cursor-pointer">Обратная связь по предыдущей версии</summary>
          <pre className="mt-1 whitespace-pre-wrap">{previous.feedback}</pre>
        </details>
      )}
    </Section>
  );
}

/**
 * Разметка персональных данных внутри блока.
 *
 * Позиции приходят с сервера и посчитаны тем же щитом, который маскирует
 * текст перед вызовом модели. Это принципиально: собственная эвристика в
 * интерфейсе рисовала бы одно, а в модель уходило бы другое, и подсветка
 * стала бы декорацией вместо доказательства.
 *
 * Показывается исходное значение, а не псевдоним: ревьюеру нужно видеть, что
 * именно защищено, — а в модель ушёл `ЛИЦО_1`.
 */
/**
 * Отрисовка блока работы по его типу.
 *
 * Раньше всё, кроме таблиц, печаталось одной строкой обычного текста —
 * заголовки, списки, диаграммы, код. Работа выглядела как транскрипт, и
 * читать её было невозможно: ревьюер листает документ студента, а видит
 * поток символов. Тип блока читатель определяет ещё при разборе
 * (`heading`, `list_item`, `table`, `sheet`, `code`), но интерфейс им не
 * пользовался вовсе.
 *
 * Markdown-таблицы отдельный случай: в `.md` они приходят обычным абзацем
 * со символами `|`, и без разбора здесь решения по системному дизайну
 * состояли из строк вида `| Компонент | Зона ответственности |---|---|`.
 */
function BlockBody({
  block,
  cls,
  showPii,
}: {
  block: any;
  cls: string;
  showPii: boolean;
}) {
  const text: string = block.text ?? "";
  const body = showPii ? <WithPii text={text} spans={block.pii} /> : null;

  // ── таблица из читателя: .docx и листы .xlsx ────────────────────────────
  if (block.table?.length) {
    const caption = block.kind === "sheet" ? text.split("\n")[0] : "";
    return (
      <div>
        {caption && <div className="muted mb-1 text-[11px]">{caption}</div>}
        <Grid rows={block.table} />
      </div>
    );
  }

  // ── код и диаграммы ────────────────────────────────────────────────────
  if (block.kind === "code") {
    // Первая строка — подпись, которую поставил читатель:
    // «Диаграмма mermaid (C4 контекстная):» или «Код (json):».
    const nl = text.indexOf("\n");
    const label = nl > 0 && text.slice(0, nl).endsWith(":") ? text.slice(0, nl) : "";
    const code = label ? text.slice(nl + 1) : text;
    return (
      <div>
        {label && (
          <div className="mb-1 text-[11px]" style={{ color: "var(--info)" }}>
            {label}
          </div>
        )}
        <pre
          className="overflow-x-auto rounded p-2 text-[11px] leading-snug"
          style={{ background: "var(--surface)", fontFamily: "ui-monospace, monospace" }}
        >
          {code}
        </pre>
      </div>
    );
  }

  // ── markdown-таблица внутри абзаца ─────────────────────────────────────
  const pipe = parsePipeTable(text);
  if (pipe) return <Grid rows={pipe} />;

  if (block.kind === "heading") {
    const level = (text.match(/^#{1,6}/)?.[0].length ?? 0) || 2;
    const clean = text.replace(/^#{1,6}\s*/, "");
    return (
      <div
        className={`font-semibold ${cls}`}
        style={{ fontSize: level <= 1 ? 16 : level === 2 ? 15 : 14 }}
      >
        {showPii ? body : renderInline(clean)}
      </div>
    );
  }

  if (block.kind === "list_item") {
    return (
      <div className={`flex gap-1.5 ${cls}`} style={{ fontSize: 13 }}>
        <span className="muted">•</span>
        <span>{showPii ? body : renderInline(text.replace(/^[-*+]\s*/, ""))}</span>
      </div>
    );
  }

  if (block.kind === "image") {
    return (
      <div className="muted text-xs" style={{ fontStyle: "italic" }}>
        {text || "изображение — текстовой моделью не оценивается"}
      </div>
    );
  }

  return (
    <div className={`whitespace-pre-wrap ${cls}`} style={{ fontSize: 13 }}>
      {showPii ? body : renderInline(text)}
    </div>
  );
}

/** Таблица: первая строка — шапка. */
function Grid({ rows }: { rows: string[][] }) {
  const [head, ...rest] = rows;
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-xs">
        <thead>
          <tr>
            {head.map((cell, i) => (
              <th
                key={i}
                className="border px-1.5 py-1 text-left font-medium"
                style={{ borderColor: "var(--border)", background: "var(--surface)" }}
              >
                {cell}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rest.map((row, ri) => (
            <tr key={ri}>
              {row.map((cell, ci) => (
                <td
                  key={ci}
                  className="border px-1.5 py-1 align-top"
                  style={{ borderColor: "var(--border)" }}
                >
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * Таблица, записанная символами `|` — как её пишут в Markdown.
 *
 * Возвращает строки без разделителя `---|---`, либо `null`, если это не
 * таблица. Требуются минимум две строки: одиночная строка с вертикальной
 * чертой — это просто предложение, а не таблица.
 */
function parsePipeTable(text: string): string[][] | null {
  const lines = text.split("\n").map((l) => l.trim()).filter(Boolean);
  if (lines.length < 2) return null;
  if (!lines.every((l) => l.includes("|"))) return null;

  const rows = lines
    .filter((l) => !/^\|?[\s:|-]+\|[\s:|-]*$/.test(l))
    .map((l) =>
      l
        .replace(/^\||\|$/g, "")
        .split("|")
        .map((c) => c.trim()),
    );
  if (rows.length < 2) return null;
  // Ширина строк должна быть согласованной, иначе это не таблица.
  const width = rows[0].length;
  if (width < 2 || rows.some((r) => Math.abs(r.length - width) > 1)) return null;
  return rows;
}

/** Простейшая разметка внутри строки: **жирный**, *курсив*, `код`. */
function renderInline(text: string): React.ReactNode {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`|\*[^*\n]+\*)/g);
  return parts.map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      return <b key={i}>{part.slice(2, -2)}</b>;
    }
    if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
      return (
        <code key={i} style={{ fontSize: "0.92em" }}>
          {part.slice(1, -1)}
        </code>
      );
    }
    if (part.startsWith("*") && part.endsWith("*") && part.length > 2) {
      return <i key={i}>{part.slice(1, -1)}</i>;
    }
    return part;
  });
}

function WithPii({ text, spans }: { text: string; spans?: any[] }) {
  if (!spans?.length) return <>{text}</>;
  const out: React.ReactNode[] = [];
  let cursor = 0;
  spans.forEach((s, i) => {
    if (s.start > cursor) out.push(text.slice(cursor, s.start));
    out.push(
      <mark
        key={i}
        className="hl-pii"
        title={`${s.label} — заменено псевдонимом до вызова модели`}
      >
        {text.slice(s.start, s.end)}
      </mark>,
    );
    cursor = s.end;
  });
  if (cursor < text.length) out.push(text.slice(cursor));
  return <>{out}</>;
}

/**
 * Просмотрщик документа по блокам §N с четырьмя слоями аннотаций.
 *
 * Подсветка идёт по номеру блока, а не по смещению в тексте: блоки —
 * та же единица, на которую ссылается модель и по которой код проверяет
 * цитаты, поэтому расхождения между «что процитировано» и «что подсвечено»
 * возникнуть не может.
 */
function DocumentViewer({
  blocks,
  review,
  layers,
  highlight,
}: {
  blocks: any[];
  review: any;
  layers: { evidence: boolean; ai: boolean; formal: boolean; pii: boolean };
  highlight: { blocks: number[]; kind: string } | null;
}) {
  const annotations = useMemo(() => {
    const map = new Map<number, { kind: string; text: string }[]>();
    const add = (block: number, kind: string, text: string) => {
      if (!map.has(block)) map.set(block, []);
      map.get(block)!.push({ kind, text });
    };

    if (layers.evidence) {
      for (const c of review.criteria ?? []) {
        for (const ev of c.evidence ?? []) {
          if (ev.status === "verified" || ev.status === "wrong_block") {
            add(ev.found_in_block ?? ev.block, "evidence", `${c.criterion_name}: подтверждает балл`);
          }
        }
      }
    }
    if (layers.formal) {
      for (const f of review.formal ?? []) {
        if (f.status === "fail") {
          for (const b of f.block_refs ?? []) add(b, "gap", `${f.title}: ${f.message}`);
        }
      }
    }
    if (layers.ai) {
      // Судья по генИИ инструктирован ссылаться на §N в цитатах, но возвращает
      // их строкой, а не структурой: обвинительный вывод намеренно не имеет
      // машинного формата, чтобы его нельзя было применить автоматически.
      // Номера блоков достаём регуляркой; не нашлись — слой просто пуст,
      // а не ломает просмотрщик.
      for (const s of review.ai_signal?.signals ?? []) {
        if (!s.available) continue;
        for (const line of s.evidence ?? []) {
          for (const m of String(line).matchAll(/§(\d+)/g)) {
            add(Number(m[1]), "ai", `${s.title}: ${String(line).slice(0, 120)}`);
          }
        }
      }
    }
    return map;
  }, [review, layers]);

  useEffect(() => {
    if (highlight?.blocks?.length) {
      document
        .getElementById(`block-${highlight.blocks[0]}`)
        ?.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }, [highlight]);

  if (!blocks.length) return <Empty>Документ не разобран.</Empty>;

  return (
    <div
      className="max-h-[600px] overflow-auto rounded p-3"
      style={{ background: "var(--surface-2)" }}
    >
      {blocks.map((b) => {
        const notes = annotations.get(b.index) ?? [];
        const focused = highlight?.blocks?.includes(b.index);
        const cls = notes.some((n) => n.kind === "evidence")
          ? "hl-evidence"
          : notes.some((n) => n.kind === "gap")
            ? "hl-gap"
            : notes.some((n) => n.kind === "ai")
              ? "hl-ai"
              : "";
        return (
          <div
            key={b.index}
            id={`block-${b.index}`}
            className="mb-2 rounded px-2 py-1"
            style={{
              outline: focused ? "2px solid var(--brand)" : "none",
              scrollMarginTop: 80,
            }}
          >
            <div className="flex gap-2">
              <span className="muted select-none text-[11px] tabular-nums">§{b.index}</span>
              <div className="flex-1">
                <BlockBody block={b} cls={cls} showPii={layers.pii} />
                {notes.map((n, i) => (
                  <div
                    key={i}
                    className="mt-0.5 text-[11px]"
                    style={{
                      color:
                        n.kind === "evidence"
                          ? "var(--ok)"
                          : n.kind === "ai"
                            ? "#a371f7"
                            : "var(--warn)",
                    }}
                  >
                    ↳ {n.text}
                  </div>
                ))}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
