/**
 * Качество ревью: результаты бенчмарка.
 *
 * Страница намеренно показывает и то, что получилось, и то, что не получилось.
 * Ранжирование 67 % и неразделённая пара «среднее — хорошее» выведены так же
 * заметно, как удачные пары: цифра, которую прячут, перестаёт быть аргументом.
 *
 * Данные читаются из готового отчёта, а не пересчитываются по нажатию: прогон
 * занимает минуты и требует запущенной модели, а страница должна открываться
 * мгновенно и работать даже при выключенном провайдере.
 */

import { useEffect, useState } from "react";

import { api } from "../api";
import { Badge, Empty, Section, Spinner } from "../components/ui";

export default function Quality() {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [effect, setEffect] = useState<any>(null);

  useEffect(() => {
    api
      .quality()
      .then(setData)
      .catch((e) => setError(String(e.message ?? e)));
    // Показатели эффекта переехали сюда из аналитики потока: там они
    // мешали смотреть на сам поток, а кейс требует измеримых показателей
    // — сокращения ручных действий, времени обработки и просроченных
    // проверок. Место для них — страница про качество работы системы.
    api
      .analytics()
      .then((a) => setEffect(a.effect))
      .catch(() => setEffect(null));
  }, []);

  if (error) return <Section title="Качество ревью"><Empty>{error}</Empty></Section>;
  if (!data) return <Section title="Качество ревью"><Spinner label="Загрузка…" /></Section>;

  if (!data.available) {
    return (
      <Section title="Качество ревью">
        <Empty>{data.hint}</Empty>
      </Section>
    );
  }

  const s = data.summary ?? {};
  const pairs: any[] = s.pairs ?? [];
  const separated = pairs.filter((p) => p.separated).length;

  return (
    <div className="flex flex-col gap-4">
      {effect && <EffectSection effect={effect} />}

      <Section
        title="Насколько точно система оценивает работы"
        right={<Badge>модель: {data.model}</Badge>}
      >
        <p className="muted mb-3 text-xs">
          Три работы одного задания — заведомо слабая, средняя и хорошая, —
          каждая прогнана {s.runs_per_case} раза. Это показатель направления,
          а не статистика: выборка мала, и выводы за её пределы не переносятся.
          Прогон от {String(data.started_at ?? "—")}.
        </p>

        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Kpi
            title="Ранжирование"
            value={`${Math.round((s.ranking_accuracy ?? 0) * 100)}%`}
            hint={`${separated} пары из ${pairs.length} упорядочены верно`}
            tone={(s.ranking_accuracy ?? 0) >= 0.99 ? "ok" : "warn"}
          />
          <Kpi
            title="Разброс между прогонами"
            value={
              Math.max(...Object.values<number>(s.stability_spread ?? { a: 0 })).toFixed(1)
            }
            hint="балла — при temperature 0 и фиксированном seed"
            tone="ok"
          />
          <Kpi
            title="Цитаты подтверждены"
            value={`${Math.round((s.evidence_verified_rate ?? 0) * 100)}%`}
            hint="найдены кодом в указанном блоке"
            tone={(s.evidence_verified_rate ?? 0) >= 0.85 ? "ok" : "warn"}
          />
          <Kpi
            title="Время на работу"
            value={`${Math.round(s.mean_duration_s ?? 0)} с`}
            hint="локальная модель, RTX 5070 Ti"
            tone="ok"
          />
        </div>
      </Section>

      <Section title="Средний балл по уровням работ">
        <table className="w-full text-sm">
          <thead className="muted text-left text-xs">
            <tr>
              <th className="py-1">работа</th>
              <th className="py-1 text-right">средний балл</th>
              <th className="py-1 text-right">разброс</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(s.mean_scores ?? {}).map(([label, score]) => (
              <tr key={label} className="border-b last:border-0" style={{ borderColor: "var(--border)" }}>
                <td className="py-1.5">{label}</td>
                <td className="py-1.5 text-right tabular-nums">{Number(score).toFixed(2)}</td>
                <td className="py-1.5 text-right tabular-nums muted">
                  {Number(s.stability_spread?.[label] ?? 0).toFixed(1)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>

      <Section title="Разделение по парам — включая неудачную">
        <table className="w-full text-sm">
          <thead className="muted text-left text-xs">
            <tr>
              <th className="py-1 w-full">пара</th>
              <th className="py-1 text-right">верных прогонов</th>
              <th className="py-1 whitespace-nowrap text-right">разрыв</th>
              <th className="py-1 whitespace-nowrap pl-6">итог</th>
            </tr>
          </thead>
          <tbody>
            {pairs.map((p) => (
              <tr key={p.pair} className="border-b last:border-0" style={{ borderColor: "var(--border)" }}>
                <td className="py-1.5">{p.pair}</td>
                <td className="py-1.5 text-right tabular-nums">
                  {p.correct_runs}/{p.runs}
                </td>
                <td className="py-1.5 whitespace-nowrap text-right tabular-nums">
                  {p.mean_gap > 0 ? "+" : ""}
                  {Number(p.mean_gap).toFixed(1)}
                </td>
                <td className="py-1.5 whitespace-nowrap pl-6">
                  <span style={{ color: p.separated ? "var(--ok)" : "var(--warn)" }}>
                    {p.separated ? "разделены" : "не разделены"}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="muted mt-3 text-xs">
          Слабая работа отделяется от обеих сильных устойчиво. Средняя и хорошая
          не различаются: разрыв 0,1 балла лежит в критерии оформления, где у
          хорошей работы нет ни одной таблицы и ни одного стиля заголовка.
          Подгонять веса под три примера мы не стали — это была бы настройка
          на выборку, а не улучшение.
        </p>
      </Section>

      <Section title="Все прогоны" defaultOpen={false}>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="muted text-left">
              <tr>
                <th className="py-1">работа</th>
                <th className="py-1 text-right">прогон</th>
                <th className="py-1 text-right">балл</th>
                <th className="py-1 text-right">цитаты</th>
                <th className="py-1 text-right">сигнал ИИ</th>
                <th className="py-1 text-right">формальные</th>
                <th className="py-1 text-right">время</th>
              </tr>
            </thead>
            <tbody>
              {(data.runs ?? []).map((r: any, i: number) => (
                <tr key={i} className="border-b last:border-0" style={{ borderColor: "var(--border)" }}>
                  <td className="py-1">{r.label}</td>
                  <td className="py-1 text-right tabular-nums">{r.run_index + 1}</td>
                  <td className="py-1 text-right tabular-nums">
                    {r.score}/{r.max_score}
                  </td>
                  <td className="py-1 text-right tabular-nums">
                    {r.evidence_verified}/{r.evidence_total}
                  </td>
                  <td className="py-1 text-right tabular-nums">{Number(r.ai_score).toFixed(2)}</td>
                  <td className="py-1 text-right tabular-nums">{r.formal_violations}</td>
                  <td className="py-1 text-right tabular-nums">{Math.round(r.duration_s)} с</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>
    </div>
  );
}

function Kpi({
  title,
  value,
  hint,
  tone,
}: {
  title: string;
  value: string;
  hint: string;
  tone: "ok" | "warn";
}) {
  return (
    <div className="rounded p-3" style={{ background: "var(--surface-2)" }}>
      <div className="muted text-xs">{title}</div>
      <div
        className="text-2xl font-semibold tabular-nums"
        style={{ color: tone === "ok" ? "var(--ok)" : "var(--warn)" }}
      >
        {value}
      </div>
      <div className="muted text-[11px]">{hint}</div>
    </div>
  );
}

/**
 * Измеримые показатели эффекта — прямой ответ на критерий успеха кейса.
 *
 * Базовая линия ручных действий выведена из семи шагов процесса AS-IS и
 * зафиксирована в `docs/as-is-to-be.md`; фактические действия считаются по
 * журналу. Числитель и знаменатель обязаны считать одни и те же работы —
 * когда-то они считали разные, и метрика показывала «сокращение −36 %»,
 * то есть заявляла, что система увеличила ручную работу.
 */
function EffectSection({ effect }: { effect: any }) {
  const pct = (v: number | null | undefined) =>
    v != null ? `${Math.round(v * 100)}%` : "—";

  const cells: [string, string, string][] = [
    ["Ручных действий было", String(effect.manual_actions_baseline ?? "—"),
     "базовая линия процесса без системы"],
    ["Ручных действий стало", String(effect.manual_actions_actual ?? "—"),
     "фактические действия пользователей из журнала"],
    ["Сокращение", pct(effect.reduction_rate),
     "на столько меньше ручных операций"],
    ["Время проверки работы",
     effect.mean_processing_seconds != null
       ? `${Math.round(effect.mean_processing_seconds)} с`
       : "—",
     "среднее время предварительной проверки"],
    ["Просроченных проверок", String(effect.overdue_reviews ?? "—"),
     "работ, не закрытых в срок проверки"],
    ["Работ обработано", String(effect.submissions_processed ?? "—"),
     "по ним и считаются числа выше"],
  ];

  return (
    <Section
      title="Эффект: измеримые показатели"
      hint="Три показателя из критериев успеха кейса: ручные действия, время обработки, просроченные проверки."
    >
      <div className="grid gap-3 sm:grid-cols-3">
        {cells.map(([label, value, hint]) => (
          <div key={label} className="rounded p-2" style={{ background: "var(--surface-2)" }}>
            <div className="muted text-xs">{label}</div>
            <div className="text-xl font-semibold tabular-nums">{value}</div>
            <div className="muted text-[11px]">{hint}</div>
          </div>
        ))}
      </div>
      <p className="muted mt-2 text-xs">
        Базовая линия выведена из семи шагов текущего процесса и зафиксирована
        в <code>docs/as-is-to-be.md</code>. Фактические действия берутся из
        журнала действий пользователей, а не оцениваются.
      </p>
    </Section>
  );
}
