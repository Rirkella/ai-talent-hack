/**
 * Настройка формулы: веса и тумблеры индекса приоритета.
 *
 * Правка применяется к уже готовым ревью сразу — методист двигает вес и
 * видит, как переупорядочилась очередь, а не ждёт нового прогона модели.
 *
 * Ключевое ограничение кейса выведено прямо в интерфейс, а не только в
 * документацию: веса влияют ТОЛЬКО на приоритет ручной проверки. Сигнал
 * генеративного ИИ не входит в балл ни при каком положении тумблера.
 */

import { useCallback, useEffect, useState } from "react";

import { api } from "../api";
import { Badge, Section, Spinner } from "../components/ui";

type Toast = (t: string, tone?: "info" | "ok" | "warn" | "err") => void;

export default function Scoring({
  assignmentId,
  toast,
  onChanged,
}: {
  assignmentId: string;
  toast: Toast;
  onChanged: () => void;
}) {
  const [data, setData] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [presets, setPresets] = useState<any[]>([]);
  const [newName, setNewName] = useState("");
  // Позиция ползунка, пока его тянут. Значение с сервера — «истина», но во
  // время перетаскивания истина отстаёт на один запрос, и без локального
  // состояния ползунок дёргался бы назад после каждого шага.
  const [draft, setDraft] = useState<Record<string, number>>({});
  // Итог последнего пересчёта показывается строкой рядом с заголовком, а не
  // всплывающим сообщением: движений ползунка много, и каждое давало карточку.
  const [applied, setApplied] = useState<string>("");

  const load = useCallback(() => {
    api.scoring().then(setData).catch(() => setData(null));
    api.presets().then(setPresets).catch(() => setPresets([]));
  }, []);
  useEffect(load, [load]);

  if (!data) {
    return (
      <Section title="Формула приоритета" defaultOpen={false}>
        <Spinner label="Загрузка…" />
      </Section>
    );
  }

  const apply = (patch: Record<string, unknown>) => {
    setBusy(true);
    api
      .setScoring(assignmentId, patch)
      .then((r) => {
        setApplied(
          r.recalculated > 0
            ? `пересчитано работ: ${r.recalculated}`
            : "сохранено, пересчитывать нечего",
        );
        setDraft({});
        load();
        onChanged();
      })
      .catch((e) => toast(String(e.message ?? e), "err"))
      .finally(() => setBusy(false));
  };

  const run = (p: Promise<any>, ok: (r: any) => string) => {
    setBusy(true);
    p.then((r) => {
      toast(ok(r), "ok");
      load();
      onChanged();
    })
      .catch((e) => toast(String(e.message ?? e), "err"))
      .finally(() => setBusy(false));
  };

  const entries = Object.entries(data.weights as Record<string, any>);
  const weightOf = (key: string, v: any): number => draft[key] ?? v.weight;
  const total = entries
    .filter(([, v]) => v.on)
    .reduce((a, [key, v]) => a + weightOf(key, v), 0);

  return (
    <Section
      title="Формула приоритета ручной проверки"
      defaultOpen={false}
      right={
        <div className="flex items-center gap-2">
          {applied && <span className="muted text-xs">{applied}</span>}
          <Badge>сумма активных весов: {total.toFixed(2)}</Badge>
        </div>
      }
    >
      <div
        className="mb-3 rounded p-2 text-xs"
        style={{ background: "var(--surface-2)", color: "var(--text-dim)" }}
      >
        {data.note}
      </div>

      <div className="mb-4 border-b pb-3" style={{ borderColor: "var(--border)" }}>
        <div className="mb-1 text-sm font-medium">Готовые наборы настроек</div>
        <p className="muted mb-2 text-xs">
          Набор задаёт все веса разом и применяется к потоку сразу.
          Встроенные наборы удалить нельзя — это способ вернуться к
          исходным настройкам, если ползунки увели далеко.
        </p>
        <div className="flex flex-wrap items-center gap-2">
          {presets.map((p) => (
            <span key={p.name} className="flex items-center">
              <button
                className="btn text-xs"
                disabled={busy}
                onClick={() =>
                  run(api.applyPreset(p.name, assignmentId), (r) =>
                    `Набор «${p.name}» применён, пересчитано работ: ${r.recalculated}`,
                  )
                }
                title={p.builtin ? "встроенный набор" : "ваш сохранённый набор"}
              >
                {p.name}
              </button>
              {!p.builtin && (
                <button
                  className="btn ml-1 px-1.5 text-xs"
                  disabled={busy}
                  title="Удалить набор"
                  onClick={() =>
                    run(api.deletePreset(p.name), () => `Набор «${p.name}» удалён`)
                  }
                >
                  ×
                </button>
              )}
            </span>
          ))}
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <input
            className="input max-w-[220px]"
            placeholder="Название нового набора"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
          />
          <button
            className="btn text-xs"
            disabled={busy || !newName.trim()}
            onClick={() =>
              run(api.savePreset(newName.trim()), () => {
                setNewName("");
                return `Набор «${newName.trim()}» сохранён`;
              })
            }
          >
            Сохранить текущие настройки
          </button>
        </div>
      </div>

      <div className="flex flex-col gap-2">
        {entries.map(([key, v]) => (
          <div key={key} className="flex flex-wrap items-center gap-3 text-sm">
            <label className="flex w-64 cursor-pointer items-center gap-2">
              <input
                type="checkbox"
                checked={v.on}
                disabled={busy}
                onChange={(e) => apply({ toggles: { [key]: e.target.checked } })}
              />
              <span style={{ opacity: v.on ? 1 : 0.5 }}>{v.title}</span>
            </label>

            {/*
              Ползунок НЕ блокируется во время запроса. Раньше `disabled`
              выставлялся на время каждого запроса, а запрос уходил на
              каждое движение: браузер снимал захват мыши с заблокированного
              элемента, перетаскивание обрывалось после одного деления, и
              дальше приходилось заново нажимать кнопку мыши.

              Теперь движение меняет только локальное значение, а на сервер
              уходит одно значение — то, на котором ползунок отпустили.
            */}
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={weightOf(key, v)}
              disabled={!v.on}
              className="flex-1"
              onChange={(e) =>
                setDraft((d) => ({ ...d, [key]: Number(e.target.value) }))
              }
              onPointerUp={() => {
                const val = draft[key];
                if (val != null && val !== v.weight) apply({ weights: { [key]: val } });
              }}
              onKeyUp={() => {
                const val = draft[key];
                if (val != null && val !== v.weight) apply({ weights: { [key]: val } });
              }}
            />
            <span className="w-12 text-right tabular-nums" style={{ opacity: v.on ? 1 : 0.5 }}>
              {weightOf(key, v).toFixed(2)}
            </span>
          </div>
        ))}
      </div>

      <div className="mt-4 border-t pt-3" style={{ borderColor: "var(--border)" }}>
        <div className="mb-1 text-sm font-medium">Штраф за нарушения оформления</div>
        <p className="muted mb-2 text-xs">
          Единственный настраиваемый компонент, который меняет <b>балл</b>, а не
          приоритет. По умолчанию выключен: условие задания такого штрафа не
          предусматривает, а критерий «Качество оформления» уже оценивается отдельно.
        </p>
        <label className="flex cursor-pointer items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={data.formal_penalty_on}
            disabled={busy}
            onChange={(e) => apply({ formal_penalty_on: e.target.checked })}
          />
          Применять штраф
          <span className="muted text-xs">
            {data.formal_penalty_per_violation} балла за нарушение, максимум{" "}
            {data.formal_penalty_max}
          </span>
        </label>
      </div>
    </Section>
  );
}
