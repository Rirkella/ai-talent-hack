/**
 * Вкладка «Приватность» — доказательство мер защиты данных.
 *
 * Кейс требует описать меры защиты. Одних слов мало, поэтому здесь
 * показываются не декларации, а числа: счётчик фактически перехваченных
 * вызовов, журнал обращений с хостами, что именно нашёл ПДн-щит.
 *
 * Это финальный кадр демонстрации: «внешних вызовов — ноль».
 */

import { useCallback, useEffect, useState } from "react";

import { api } from "../api";
import { Badge, Empty, Section, Spinner } from "../components/ui";

export default function Privacy({ tick }: { tick: number }) {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    api
      .privacy()
      .then(setData)
      .catch((e) => setError(String(e.message ?? e)));
  }, []);

  useEffect(load, [load, tick]);

  if (error) {
    return (
      <div className="card" style={{ borderColor: "var(--err)" }}>
        <span style={{ color: "var(--err)" }}>{error}</span>
      </div>
    );
  }
  if (!data) return <Spinner label="Загрузка…" />;

  const a = data.audit ?? {};
  const external = a.external_calls ?? 0;
  const blocked = a.blocked_calls ?? 0;

  return (
    <div className="flex flex-col gap-4">
      {/* Главное число вкладки — крупно и первым. */}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Metric
          label="Внешних вызовов"
          value={external}
          tone={external === 0 ? "ok" : "err"}
          hint="фактически перехваченные обращения за пределы контура"
        />
        <Metric
          label="Внутренних вызовов"
          value={a.internal_calls ?? 0}
          hint="обращения к локальной модели"
        />
        <Metric
          label="Заблокировано"
          value={blocked}
          tone={blocked > 0 ? "warn" : "default"}
          hint="попытки выйти за пределы ALLOWED_HOSTS"
        />
        <Metric
          label="Модель"
          value={a.llm_is_local ? "локально" : "внешняя"}
          tone={a.llm_is_local ? "ok" : "err"}
          hint={(a.allowed_hosts ?? []).join(", ")}
        />
      </div>

      <Section title="Офлайн-контур">
        <div className="flex flex-col gap-2 text-sm">
          <Row
            label="Жёсткий режим (OFFLINE_ENFORCE)"
            value={
              a.offline_enforce ? (
                <Badge tone="ok">включён — вызов наружу роняется</Badge>
              ) : (
                <Badge tone="warn">выключен — только запись в журнал</Badge>
              )
            }
          />
          <Row
            label="Разрешённые хосты"
            value={
              <span className="font-mono text-xs">
                {(a.allowed_hosts ?? []).join(", ") || "—"}
              </span>
            }
          />
          <Row
            label="Псевдонимизация ПДн"
            value={
              data.pii_enabled ? (
                <Badge tone="ok">включена, до вызова модели</Badge>
              ) : (
                <Badge tone="err">выключена</Badge>
              )
            }
          />
          <Row
            label="NER для имён (natasha)"
            value={
              data.pii_use_ner ? (
                <Badge tone="ok">включён</Badge>
              ) : (
                <Badge tone="warn">выключен — работают только регулярки</Badge>
              )
            }
          />
        </div>
        <p className="muted mt-3 text-xs">
          Каждое обращение к модели проходит через страж: хост вне списка
          разрешённых роняет вызов <b>до его выполнения</b>. Число внешних вызовов
          выше — из журнала фактических обращений, а не из настроек.
        </p>
      </Section>

      <Section title="Что нашёл ПДн-щит">
        <p className="muted mb-3 text-xs">
          Просканировано документов: {data.documents_scanned}. Маскирование
          выполняется до вызова модели; карта псевдонимов локальна и применяется
          в обратную сторону при выдаче обратной связи студенту.
        </p>
        {Object.keys(data.pii_found ?? {}).length === 0 ? (
          <Empty>Персональных данных в загруженных работах не найдено.</Empty>
        ) : (
          <div className="flex flex-wrap gap-2">
            {Object.entries(data.pii_found as Record<string, number>).map(([k, v]) => (
              <div
                key={k}
                className="rounded px-2 py-1 text-xs"
                style={{ background: "var(--surface-2)" }}
              >
                <span className="muted">{k}: </span>
                <span className="font-semibold tabular-nums">{v}</span>
              </div>
            ))}
          </div>
        )}
        <p className="muted mt-3 text-xs">
          Отдельно маскируются <b>метаданные документа</b>: ФИО автора лежит в
          <code> docProps</code>, а не в тексте, и без этого шага утекло бы именно оно.
        </p>
      </Section>

      <Section title={`Журнал вызовов (${(data.recent_calls ?? []).length})`} defaultOpen={false}>
        {(data.recent_calls ?? []).length === 0 && <Empty>Вызовов пока не было.</Empty>}
        <div className="max-h-96 overflow-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b text-left muted" style={{ borderColor: "var(--border)" }}>
                <th className="py-1 font-normal">Время</th>
                <th className="py-1 font-normal">Хост</th>
                <th className="py-1 font-normal">Тип</th>
                <th className="py-1 font-normal">Цель</th>
                <th className="py-1 text-right font-normal">Длительность</th>
              </tr>
            </thead>
            <tbody>
              {(data.recent_calls ?? []).map((c: any, i: number) => (
                <tr key={i} className="border-b last:border-0" style={{ borderColor: "var(--border)" }}>
                  <td className="py-1 tabular-nums muted">
                    {new Date(c.at).toLocaleTimeString("ru-RU")}
                  </td>
                  <td className="py-1 font-mono">{c.host}</td>
                  <td className="py-1">
                    <Badge
                      tone={c.kind === "internal" ? "ok" : c.kind === "blocked" ? "err" : "warn"}
                    >
                      {c.kind === "internal"
                        ? "внутренний"
                        : c.kind === "blocked"
                          ? "заблокирован"
                          : "внешний"}
                    </Badge>
                  </td>
                  <td className="py-1 muted">{c.purpose}</td>
                  <td className="py-1 text-right tabular-nums muted">
                    {c.duration_ms != null ? `${c.duration_ms} мс` : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>

      <div className="card" style={{ borderColor: external === 0 ? "var(--ok)" : "var(--warn)" }}>
        <div className="text-sm">
          <b>Что это доказывает.</b> Данные студентов не покидают машину: единственный
          адрес, к которому обращается приложение, — локальная модель. В метаданных
          реальных работ лежат настоящие ФИО, поэтому псевдонимизация выполняется
          всегда и до обращения к модели, а не после.
        </div>
        <div className="muted mt-2 text-xs">
          Ограничения и упрощения перечислены в <code>docs/security.md</code>: в MVP нет
          аутентификации и шифрования БД, и это указано прямо, а не умолчанием.
        </div>
      </div>
    </div>
  );
}

function Metric({
  label,
  value,
  hint,
  tone = "default",
}: {
  label: string;
  value: number | string;
  hint?: string;
  tone?: "default" | "ok" | "warn" | "err";
}) {
  const color =
    tone === "ok"
      ? "var(--ok)"
      : tone === "err"
        ? "var(--err)"
        : tone === "warn"
          ? "var(--warn)"
          : "var(--text)";
  return (
    <div className="card" title={hint}>
      <div className="muted text-xs">{label}</div>
      <div className="text-2xl font-semibold tabular-nums" style={{ color }}>
        {value}
      </div>
      {hint && <div className="muted mt-0.5 text-[11px]">{hint}</div>}
    </div>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2">
      <span className="muted text-xs">{label}</span>
      {value}
    </div>
  );
}
