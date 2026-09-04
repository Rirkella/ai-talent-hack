/**
 * Аналитика прогресса и метрики эффекта.
 *
 * Формулировки метрик взяты дословно из критериев успеха кейса: сокращение
 * числа ручных действий, время обработки, количество просроченных проверок.
 * Числа берутся из журнала фактических действий, а не из оценки на глаз —
 * иначе метрику нечем подтвердить.
 */

import { useEffect, useState } from "react";

import { api } from "../api";
import { DayBars, Funnel, RankedBars, Strip } from "../components/charts";
import { Badge, Bar, Empty, Section, Spinner } from "../components/ui";
import StudentProfile from "./StudentProfile";

export default function Analytics({
  assignmentId,
  tick,
}: {
  assignmentId: string;
  tick: number;
}) {
  const [data, setData] = useState<any>(null);
  // Профиль открывается по явному выбору, а не грузится для всех сразу:
  // на потоке в сотню студентов это была бы сотня лишних запросов.
  const [student, setStudent] = useState<string>("");
  const [students, setStudents] = useState<{ id: string; name: string }[]>([]);

  useEffect(() => {
    api
      .users()
      .then((us) => setStudents(us.filter((u: any) => u.role === "student")))
      .catch(() => setStudents([]));
  }, []);

  useEffect(() => {
    api.analytics(assignmentId).then(setData).catch(() => setData(null));
  }, [assignmentId, tick]);

  if (!data) return <Section title="Аналитика"><Spinner label="Загрузка…" /></Section>;

  const f = data.funnel;


  return (
    <>
      <Section
        title="Аналитика потока"
        hint="Сколько работ сдано, сколько проверено, как распределились баллы."
      >
        <div className="grid gap-4 md:grid-cols-2">
          <div>
            <div className="mb-2 text-xs font-medium">Воронка проверки</div>
            <Funnel
              steps={[
                { label: "Загружено работ", value: f.uploaded },
                { label: "Распределено", value: f.assigned },
                { label: "Проверено автоматикой", value: f.ai_reviewed },
                { label: "Подтверждено ревьюером", value: f.confirmed },
              ]}
            />
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <Kpi label="Загружено работ" value={f.uploaded} />
            <Kpi label="Распределено" value={f.assigned} of={f.uploaded} />
            <Kpi label="Проверено автоматикой" value={f.ai_reviewed} of={f.uploaded} />
            <Kpi label="Подтверждено ревьюером" value={f.confirmed} of={f.uploaded} />
          </div>
        </div>

        <div className="mt-4 grid gap-4 md:grid-cols-2">
          <div>
            <div className="mb-2 text-xs font-medium">Состояния по срокам</div>
            <div className="flex flex-col gap-1">
              {Object.entries(data.deadline_states as Record<string, number>).map(([k, v]) => (
                <div key={k} className="flex items-center gap-2 text-xs">
                  <span className="w-40">{DEADLINE_LABELS[k] ?? k}</span>
                  <div className="flex-1">
                    <Bar
                      value={v}
                      max={Math.max(1, f.uploaded)}
                      tone={k === "late_zero" ? "err" : k === "late_penalty" ? "warn" : "ok"}
                    />
                  </div>
                  <span className="w-6 text-right tabular-nums">{v}</span>
                </div>
              ))}
            </div>
          </div>

          <div>
            <div className="mb-2 text-xs font-medium">Распределение баллов</div>
            {/*
              Сырые баллы складываются только внутри одного задания: максимум
              у продуктового фрода 10, у QA 20, у системного дизайна 6, и
              «средний балл 11,3» по такому набору не значит ничего. Когда
              заданий несколько, шкала переводится в доли от максимума.
            */}
            {data.scores.single_scale ? (
              <Histogram
                values={data.scores.final?.length ? data.scores.final : data.scores.preliminary}
              />
            ) : (
              <Histogram
                values={(data.scores.shares ?? []).map((x: number) => x * 10)}
                unit="доля от максимума, ×10"
              />
            )}
            <div className="muted mt-1 text-xs">
              {!data.scores.single_scale
                ? `доли от максимума по ${data.scores.shares?.length ?? 0} работам разных заданий` +
                  ` · средняя ${Math.round((data.scores.mean_share ?? 0) * 100)}%`
                : data.scores.final?.length
                ? `итоговые баллы, среднее ${data.scores.mean_final?.toFixed(1)}`
                : data.scores.preliminary?.length
                  ? `предварительные баллы, среднее ${data.scores.mean_preliminary?.toFixed(1)}`
                  : "данных пока нет"}
            </div>
          </div>
        </div>
      </Section>

      <Section
        title="Где проседает поток"
        hint="Критерии, по которым студенты набирают меньше всего. Кандидаты на доработку материала курса."
        defaultOpen={false}
      >
        <p className="muted mb-2 text-xs">
          Средняя доля от максимума по каждому критерию, от худшего к лучшему.
          Именно доля, а не сырой балл: критерии весят по-разному, и на
          абсолютной шкале самый дорогой всегда выглядел бы худшим. Справа —
          доля цитат, которые код нашёл в работе: низкая означает не слабую
          работу, а слабое место самого механизма доказательств.
        </p>
        <RankedBars
          items={(data.by_criterion ?? []).map((c: any) => ({
            label: c.name,
            value: c.mean_share,
            note:
              c.verified_rate == null
                ? "цитат нет"
                : `цитат ${Math.round(c.verified_rate * 100)}%`,
            tone: c.mean_share < 0.5 ? "warn" : c.mean_share < 0.75 ? undefined : "ok",
          }))}
        />
      </Section>

      <Section
        title="Что требует внимания человека"
        hint="Разбивка очереди: сколько работ автоматика проверила уверенно, а сколько отдала человеку."
        defaultOpen={false}
      >
        <p className="muted mb-2 text-xs">
          Сколько работ в каждой зоне. Границы те же, что в очереди ревьюера —
          «высоким» интерфейс и аналитика обязаны называть одно и то же.
        </p>
        <RankedBars
          items={[
            ["высокий — смотреть первым", "high", "err"],
            ["средний", "medium", "warn"],
            ["низкий", "low", "ok"],
          ].map(([label, key, tone]: any) => {
            const total =
              (data.priority_buckets?.high ?? 0) +
              (data.priority_buckets?.medium ?? 0) +
              (data.priority_buckets?.low ?? 0);
            const n = data.priority_buckets?.[key] ?? 0;
            return { label, value: total ? n / total : 0, note: `${n} работ`, tone };
          })}
        />
      </Section>

      <Section
        title="Насколько ревьюеры согласны с автоматикой"
        hint="Как часто человек оставляет предварительный балл без правки. Низкое согласие — повод пересмотреть критерии."
        defaultOpen={false}
      >
        <p className="muted mb-2 text-xs">
          Важно не только сколько раз балл правили, но и на сколько: правка на
          полбалла и правка на четыре — разные истории. Каждая точка — одна
          подтверждённая работа, положение — насколько человек сдвинул
          предварительный балл.
        </p>
        <div className="mb-3 flex flex-wrap gap-4 text-xs">
          <span>
            подтверждено без правки:{" "}
            <b className="tabular-nums">{data.agreement?.without_edit ?? 0}</b>
          </span>
          <span>
            с правкой: <b className="tabular-nums">{data.agreement?.with_edit ?? 0}</b>
          </span>
          <span className="muted">
            средняя правка: {data.agreement?.mean_abs_delta ?? 0} балла
          </span>
        </div>
        <Strip
          values={data.agreement?.deltas ?? []}
          zero
          format={(v) => `${v > 0 ? "+" : ""}${v} балла к предварительному`}
        />
      </Section>

      <Section title="Сколько времени занимает проверка" defaultOpen={false}>
        <p className="muted mb-2 text-xs">
          Каждая точка — одна работа. Для десятка работ гистограмма врёт: в
          корзине одно-два значения, и форма распределения дорисовывается
          воображением.
        </p>
        <Strip values={data.durations ?? []} format={(v) => `${v} с`} />
      </Section>

      <Section
        title="Сдачи по дням"
        hint="Когда студенты сдают работы. Пик у дедлайна — сигнал сдвинуть срок или напоминания."
        defaultOpen={false}
      >
        <p className="muted mb-2 text-xs">
          Дни идут подряд, включая пустые: пропуск дня без сдач превратил бы
          провал в потоке в ровный график.
        </p>
        <DayBars data={data.by_day} />
      </Section>

      <Section
        title="Загрузка ревьюеров"
        hint="Сколько работ у каждого при его ёмкости. Перекос лечится распределением."
        defaultOpen={false}
      >
        {data.by_reviewer.length === 0 && <Empty>Работы не распределены.</Empty>}
        <div className="flex flex-col gap-2">
          {data.by_reviewer.map((r: any) => (
            <div key={r.name} className="text-xs">
              <div className="mb-0.5 flex justify-between">
                <span>{r.name}</span>
                <span className="tabular-nums">
                  проверено {r.confirmed} из {r.assigned} · ёмкость {r.capacity}
                </span>
              </div>
              <Bar value={r.assigned} max={Math.max(r.capacity, r.assigned, 1)} />
            </div>
          ))}
        </div>
      </Section>

      <Section
        title="Типовые ошибки потока"
        hint="Что чаще всего нарушают: общие для потока пробелы, которые стоит разобрать на занятии."
        defaultOpen={false}
      >
        <p className="muted mb-2 text-xs">
          Кластеризация пробелов со всех проверенных работ. Частая ошибка — сигнал о том,
          что тему стоит подробнее раскрыть в учебных материалах.
        </p>
        {data.common_gaps.length === 0 && (
          <Empty>Повторяющихся пробелов пока не найдено — нужно больше проверенных работ.</Empty>
        )}
        <ul className="flex flex-col gap-1">
          {data.common_gaps.map((g: any, i: number) => (
            <li key={i} className="flex items-start gap-2 text-xs">
              <Badge tone="warn">×{g.count}</Badge>
              <span>{g.pattern}</span>
            </li>
          ))}
        </ul>
      </Section>

      <Section
        title="Профиль студента"
        hint="Динамика баллов одного студента и его сильные и слабые критерии."
        defaultOpen={false}
      >
        <label className="mb-3 block text-xs">
          <span className="muted">Студент</span>
          <select
            className="input mt-1 max-w-sm"
            value={student}
            onChange={(e) => setStudent(e.target.value)}
          >
            <option value="">— выберите —</option>
            {students.map((u) => (
              <option key={u.id} value={u.id}>
                {u.name}
              </option>
            ))}
          </select>
        </label>
        {student ? (
          <StudentProfile studentId={student} tick={tick} />
        ) : (
          <Empty>Выберите студента, чтобы увидеть динамику и профиль по критериям.</Empty>
        )}
      </Section>
    </>
  );
}

const DEADLINE_LABELS: Record<string, string> = {
  on_time: "в срок",
  due_soon: "скоро дедлайн",
  late_penalty: "просрочено, штраф −1",
  late_zero: "просрочено, 0 баллов",
};

function Kpi({
  label,
  value,
  of,
  hint,
  tone = "default",
}: {
  label: string;
  value: number | string;
  of?: number;
  hint?: string;
  tone?: "default" | "ok" | "err";
}) {
  const color =
    tone === "ok" ? "var(--ok)" : tone === "err" ? "var(--err)" : "var(--text)";
  return (
    <div className="rounded-lg border p-3" style={{ borderColor: "var(--border)" }} title={hint}>
      <div className="muted text-xs">{label}</div>
      <div className="text-2xl font-semibold tabular-nums" style={{ color }}>
        {value}
        {of != null && of > 0 && (
          <span className="muted text-sm"> / {of}</span>
        )}
      </div>
      {hint && <div className="muted mt-0.5 text-[11px]">{hint}</div>}
    </div>
  );
}

/** Гистограмма баллов инлайновым SVG — без графической библиотеки. */
function Histogram({ values, unit }: { values?: number[]; unit?: string }) {
  if (!values?.length) return <Empty>Нет данных.</Empty>;

  const bins = new Array(11).fill(0);
  for (const v of values) bins[Math.max(0, Math.min(10, Math.round(v)))] += 1;
  const max = Math.max(...bins, 1);
  const w = 300;
  const h = 90;
  const bw = w / bins.length;

  return (
    <svg
      viewBox={`0 0 ${w} ${h + (unit ? 28 : 16)}`}
      className="w-full"
      role="img"
      aria-label={unit ? `Гистограмма: ${unit}` : "Гистограмма баллов"}
    >
      {bins.map((n, i) => {
        const bh = (n / max) * h;
        return (
          <g key={i}>
            <rect
              x={i * bw + 1}
              y={h - bh}
              width={bw - 2}
              height={bh}
              fill="var(--brand)"
              rx={2}
            >
              <title>{`${i} баллов: ${n}`}</title>
            </rect>
            <text
              x={i * bw + bw / 2}
              y={h + 12}
              textAnchor="middle"
              fontSize={9}
              fill="var(--text-dim)"
            >
              {i}
            </text>
          </g>
        );
      })}
      {unit && (
        <text x={w / 2} y={h + 26} textAnchor="middle" fontSize={8} fill="var(--text-dim)">
          {unit}
        </text>
      )}
    </svg>
  );
}
