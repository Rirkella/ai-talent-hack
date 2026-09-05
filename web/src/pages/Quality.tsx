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

  useEffect(() => {
    api
      .quality()
      .then(setData)
      .catch((e) => setError(String(e.message ?? e)));
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

        {/*
          Происхождение чисел. Без него отчёт нельзя ни повторить, ни
          сопоставить с тем, что показывает приложение: два прогона на
          разных рубриках выглядят одинаково, а расходятся на балл.
        */}
        {data.provenance && (
          <details className="mb-3">
            <summary className="muted cursor-pointer text-xs">
              На чём считалось: рубрика, файлы, настройки модели
            </summary>
            <div className="muted mt-2 grid gap-1 text-[11px] sm:grid-cols-2">
              <div>
                критериев {data.provenance.rubric_criteria}, максимум{" "}
                {data.provenance.rubric_total_max}, отпечаток рубрики{" "}
                <code>{data.provenance.rubric_fingerprint}</code>
              </div>
              <div>
                модель <code>{data.provenance.model}</code>, temperature{" "}
                {data.provenance.temperature}, seed {data.provenance.seed},
                контекст {data.provenance.num_ctx}
              </div>
              <div>
                условие <code>{data.provenance.condition?.name}</code> (
                {String(data.provenance.condition?.sha256).slice(0, 12)}…)
              </div>
              <div>
                отпечаток промптов извлечения{" "}
                <code>{data.provenance.extract_prompts_sha256}</code>
              </div>
              <div className="sm:col-span-2">{data.provenance.evidence_rule}</div>
            </div>
          </details>
        )}

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
            title="Цитаты дословны"
            value={`${Math.round((s.evidence_verified_rate ?? 0) * 100)}%`}
            hint="найдены кодом дословно в указанном блоке"
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

      {/*
        Что стало с каждой цитатой. Одной доли «подтверждено» мало:
        «не найдено в работе» и «найдено, но в другом блоке» — разные по
        тяжести случаи, а «похоже, но не дословно» вообще не подтверждение.
      */}
      {s.evidence_breakdown && (
        <Section
          title="Что стало с цитатами модели"
          hint="Каждый балл модель обязана подкрепить цитатой с номером блока. Код ищет эту цитату в работе. Зелёным считается только дословное вхождение: цитата с отброшенной частицей «не» набирает 94 % сходства и смысл при этом переворачивает."
        >
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {(
              [
                ["verified", "Дословно в своём блоке", "подтверждение", "ok"],
                ["wrong_block", "Дословно, но в другом блоке", "ошибка в номере", "warn"],
                ["approximate", "Похоже, но не дословно", "нужен взгляд человека", "warn"],
                ["not_found", "В работе не найдено", "выдумка или склейка из разных мест", "err"],
              ] as const
            ).map(([key, title, note, tone]) => {
              const n = s.evidence_breakdown[key] ?? 0;
              const total = s.evidence_breakdown.total || 1;
              return (
                <div key={key} className="rounded p-3" style={{ background: "var(--surface-2)" }}>
                  <div className="muted text-xs">{title}</div>
                  <div
                    className="text-2xl font-semibold tabular-nums"
                    style={{
                      color:
                        tone === "ok"
                          ? "var(--ok)"
                          : tone === "warn"
                            ? "var(--warn)"
                            : "var(--err)",
                    }}
                  >
                    {n}
                    <span className="muted text-sm"> · {Math.round((n / total) * 100)}%</span>
                  </div>
                  <div className="muted text-[11px]">{note}</div>
                </div>
              );
            })}
          </div>
          <p className="muted mt-2 text-xs">
            Всего цитат: {s.evidence_breakdown.total}. Неподтверждённая цитата
            балл не снижает — она поднимает работу в очереди ручной проверки.
          </p>
        </Section>
      )}

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
