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
  const maxScore = Math.max(
    10,
    ...(data.timeline ?? []).map((t: any) => t.max_score ?? 0),
  );

  return (
    <Section title={`${title}: ${data.student?.name ?? ""}`}>
      <div className="mb-3 flex flex-wrap gap-4 text-sm">
        <Stat label="работ" value={String(s.submissions ?? 0)} />
        <Stat label="подтверждено" value={String(s.confirmed ?? 0)} />
        <Stat label="средний балл" value={s.mean_score !== null ? String(s.mean_score) : "—"} />
        <Stat label="лучший" value={s.best !== null ? String(s.best) : "—"} />
        <Stat label="со штрафом за срок" value={String(s.late ?? 0)} />
      </div>

      {/*
        Колонки в ряд не ставим: у линии динамики высота фиксированная и
        небольшая, у радара с таблицей — втрое больше, и рядом слева
        оставалось пустое поле в пол-экрана. Блоки идут друг под другом.
      */}
      <div className="flex flex-col gap-5">
        <div>
          <div className="mb-1 text-sm font-medium">Динамика баллов</div>
          <p className="muted mb-1 text-xs">
            По подтверждённому баллу, если он есть; иначе по предварительному.
          </p>
          {scored.length ? (
            <TrendLine
              max={maxScore}
              points={scored.map((t: any) => ({
                label: `в${t.version}`,
                value: Number(t.score),
                hint:
                  `${t.assignment_title || "работа"}, версия ${t.version}: ` +
                  `${t.score} из ${t.max_score ?? "—"}` +
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
