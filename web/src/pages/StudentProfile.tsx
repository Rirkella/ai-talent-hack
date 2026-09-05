/**
 * Профиль студента: динамика баллов и профиль по критериям.
 *
 * Один компонент на две роли. Методист открывает его из аналитики по любому
 * студенту, студент видит только свой — разграничение делает сервер, а не
 * интерфейс: чужой профиль отдаётся с 403 даже при прямом запросе.
 *
 * Радар строится только по подтверждённым работам. Показывать студенту его
 * «сильные и слабые стороны» по неподтверждённой автоматике нельзя: решение
 * по баллу ещё не принято человеком, а такая картинка выглядит как приговор.
 */

import { useEffect, useState } from "react";

import { api } from "../api";
import { RadarChart, TrendLine } from "../components/charts";
import { Empty, Section, Spinner } from "../components/ui";

export default function StudentProfile({
  studentId,
  tick,
  title = "Профиль студента",
}: {
  studentId: string;
  tick?: number;
  title?: string;
}) {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!studentId) return;
    api
      .studentProfile(studentId)
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((e) => setError(String(e.message ?? e)));
  }, [studentId, tick]);

  if (error) return <Section title={title}><Empty>{error}</Empty></Section>;
  if (!data) return <Section title={title}><Spinner label="Загрузка…" /></Section>;

  const s = data.summary ?? {};
  const scored = (data.timeline ?? []).filter((t: any) => t.score !== null);

  /*
    Одна шкала на графике есть только тогда, когда у всех работ одинаковый
    максимум. У студента работы могут быть с разных курсов: у продуктового
    фрода максимум 10, у QA — 20. На общей оси 17 из 20 оказывалось выше
    8 из 10, хотя это худший результат. Разные максимумы — переходим на
    доли от максимума, и подпись оси говорит об этом прямо.
  */
  const maxima = new Set<number>(scored.map((t: any) => Number(t.max_score ?? 0)));
  const singleScale = maxima.size <= 1;
  const maxScore = singleScale ? Math.max(1, ...maxima) : 100;

  return (
    <Section title={`${title}: ${data.student?.name ?? ""}`}>
      <div className="mb-3 flex flex-wrap gap-4 text-sm">
        <Stat label="работ" value={String(s.submissions ?? 0)} />
        <Stat label="подтверждено" value={String(s.confirmed ?? 0)} />
        {/*
          Средний балл показывается только внутри одной шкалы. У работ
          разных курсов максимумы разные (10 и 20), и их среднее — число
          без смысла; в этом случае показывается доля от максимума.
        */}
        <Stat
          label={s.single_scale === false ? "в среднем от максимума" : "средний балл"}
          value={
            s.single_scale === false
              ? s.mean_share != null
                ? `${Math.round(s.mean_share * 100)}%`
                : "—"
              : s.mean_score !== null
                ? String(s.mean_score)
                : "—"
          }
        />
        <Stat
          label="лучший"
          value={
            s.single_scale === false
              ? s.best_share != null
                ? `${Math.round(s.best_share * 100)}%`
                : "—"
              : s.best !== null
                ? String(s.best)
                : "—"
          }
        />
        <Stat label="со штрафом за срок" value={String(s.late ?? 0)} />
      </div>

      {/*
        Колонки в ряд не ставим: у линии динамики высота фиксированная и
        небольшая, у радара с таблицей — втрое больше, и рядом слева
        оставалось пустое поле в пол-экрана. Блоки идут друг под другом.
      */}
      <div className="flex flex-col gap-5">
        <div>
          <div className="mb-1 text-sm font-medium">
            {singleScale ? "Динамика баллов" : "Динамика: доля от максимума"}
          </div>
          <p className="muted mb-1 text-xs">
            По подтверждённому баллу, если он есть; иначе по предварительному.
            {!singleScale && (
              <>
                {" "}
                У работ разных курсов разный максимум — складывать их на одной
                шкале нельзя, поэтому здесь проценты, а сами баллы видны при
                наведении на точку.
              </>
            )}
          </p>
          {scored.length ? (
            <TrendLine
              max={maxScore}
              /* Подпись точки — дата сдачи. Раньше стояло `в${version}`,
                 то есть «версия 1» в сокращении: у работы, сданной один
                 раз, под графиком появлялось «в1» — сочетание, которое
                 читается как опечатка, а не как номер версии. */
              points={scored.map((t: any) => ({
                label: new Date(t.submitted_at).toLocaleDateString("ru-RU", {
                  day: "2-digit",
                  month: "2-digit",
                }),
                value: singleScale
                  ? Number(t.score)
                  : Math.round((Number(t.score) / (t.max_score || 1)) * 100),
                hint:
                  `${t.assignment_title || "работа"}` +
                  (t.version > 1 ? `, версия ${t.version}` : "") +
                  `: ${t.score} из ${t.max_score ?? "—"}` +
                  (t.confirmed ? " (подтверждено)" : " (предварительно)") +
                  (t.late_penalty ? `, штраф ${t.late_penalty}` : ""),
              }))}
            />
          ) : (
            <Empty>Оценённых работ пока нет.</Empty>
          )}
        </div>

        <div>
          <div className="mb-1 text-sm font-medium">Профиль по критериям</div>
          <p className="muted mb-1 text-xs">
            Доля от максимума каждого критерия. Абсолютные баллы здесь
            неинформативны: критерии весят по-разному, и самый дорогой всегда
            выглядел бы сильнейшей стороной.
          </p>
          {s.radar_available ? (
            <RadarChart items={data.radar} />
          ) : (
            <Empty>Появится после первой подтверждённой работы.</Empty>
          )}
        </div>
      </div>

      <div className="mt-4">
        <div className="mb-1 text-sm font-medium">История сдач</div>
        <table className="w-full text-xs">
          <thead className="muted text-left">
            <tr>
              <th className="w-full py-1">работа</th>
              <th className="whitespace-nowrap py-1 text-right">версия</th>
              <th className="whitespace-nowrap py-1 pl-4">сдана</th>
              <th className="whitespace-nowrap py-1 pl-4 text-right">балл</th>
              <th className="whitespace-nowrap py-1 pl-4">состояние</th>
            </tr>
          </thead>
          <tbody>
            {(data.timeline ?? []).map((t: any) => (
              <tr
                key={t.submission_id}
                className="border-b last:border-0"
                style={{ borderColor: "var(--border)" }}
              >
                <td className="py-1">{t.assignment_title || "—"}</td>
                <td className="py-1 text-right tabular-nums">{t.version}</td>
                <td className="whitespace-nowrap py-1 pl-4">
                  {new Date(t.submitted_at).toLocaleString("ru-RU")}
                </td>
                <td className="whitespace-nowrap py-1 pl-4 text-right tabular-nums">
                  {t.score !== null ? `${t.score}${t.max_score ? `/${t.max_score}` : ""}` : "—"}
                </td>
                <td className="whitespace-nowrap py-1 pl-4">
                  {t.confirmed ? (
                    <span style={{ color: "var(--ok)" }}>подтверждено</span>
                  ) : (
                    <span className="muted">на проверке</span>
                  )}
                  {t.late_penalty ? (
                    <span style={{ color: "var(--warn)" }}> · штраф {t.late_penalty}</span>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Section>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="muted text-xs">{label}</div>
      <div className="text-lg font-semibold tabular-nums">{value}</div>
    </div>
  );
}
