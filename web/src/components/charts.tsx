/**
 * Графики: инлайновый SVG без библиотек.
 *
 * Библиотеку не подключаем не из аскетизма, а по ограничению контура: внешние
 * CDN запрещены, а тянуть двести килобайт в сборку ради трёх фигур — плохой
 * обмен. Всё, что нужно этим графикам, есть в SVG.
 *
 * Общие правила, одинаковые во всех трёх:
 *
 * * цвета берутся из переменных темы (`--series-1`, `--series-2`) — они
 *   подобраны отдельно для светлой и тёмной темы и проверены на разделимость
 *   при дальтонизме, а не получены инверсией;
 * * текст никогда не красится в цвет ряда: подпись носит `--text-dim`,
 *   идентичность несёт метка рядом с ней;
 * * подсказка — нативный `<title>`: она работает и без JS, и в печати;
 * * рядом с каждым графиком есть текстовое представление тех же чисел, чтобы
 *   данные не были заперты в картинке.
 */

import { Empty } from "./ui";

const AXIS = "var(--text-dim)";
const GRID = "var(--border)";

/** Легенда. Обязательна, когда рядов больше одного. */
function Legend({ items }: { items: [string, string][] }) {
  return (
    <div className="mb-1 flex flex-wrap gap-3 text-xs" style={{ color: "var(--text-dim)" }}>
      {items.map(([label, color]) => (
        <span key={label} className="flex items-center gap-1.5">
          <span
            aria-hidden
            style={{
              width: 10, height: 10, borderRadius: 3,
              background: color, display: "inline-block",
            }}
          />
          {label}
        </span>
      ))}
    </div>
  );
}

// ── сдачи по дням ─────────────────────────────────────────────────────────────

/**
 * Столбцы по дням: сколько сдано и сколько подтверждено.
 *
 * Дни приходят с сервера подряд, включая пустые: пропуск дня без сдач
 * превратил бы провал в потоке в ровный график.
 */
export function DayBars({ data }: { data?: any[] }) {
  if (!data?.length) return <Empty>Нет сдач.</Empty>;

  const w = 520;
  const h = 110;
  const pad = { left: 22, right: 8, top: 8, bottom: 20 };
  const plotW = w - pad.left - pad.right;
  const plotH = h - pad.top - pad.bottom;

  const max = Math.max(...data.flatMap((d) => [d.submitted, d.confirmed]), 1);
  // Шкала округляется до целого: работы — штуки, дробных делений не бывает.
  const ticks = max <= 4 ? [0, max] : [0, Math.ceil(max / 2), max];

  const slot = plotW / data.length;
  // 2 px воздуха между соседними столбцами и потолок толщины: столбец не
  // должен заполнять слот целиком, иначе график выглядит стеной.
  const barW = Math.min(11, Math.max(3, slot / 2 - 2));
  const y = (v: number) => pad.top + plotH - (v / max) * plotH;

  // Подписи дат прореживаются: на потоке в месяц все тридцать не влезут.
  const step = Math.ceil(data.length / 8);

  return (
    <div>
      <Legend
        items={[
          ["сдано", "var(--series-1)"],
          ["подтверждено", "var(--series-2)"],
        ]}
      />
      <svg
        viewBox={`0 0 ${w} ${h}`}
        className="w-full"
        role="img"
        aria-label="Сдачи и подтверждения по дням"
      >
        {ticks.map((t) => (
          <g key={t}>
            <line
              x1={pad.left} x2={w - pad.right} y1={y(t)} y2={y(t)}
              stroke={GRID} strokeWidth={1}
            />
            <text x={pad.left - 5} y={y(t) + 3} textAnchor="end" fontSize={9} fill={AXIS}>
              {t}
            </text>
          </g>
        ))}

        {data.map((d, i) => {
          const x0 = pad.left + i * slot + (slot - barW * 2 - 2) / 2;
          return (
            <g key={d.date}>
              {([["submitted", "var(--series-1)", 0], ["confirmed", "var(--series-2)", 1]] as const).map(
                ([key, color, k]) => {
                  const v = d[key] as number;
                  if (!v) return null;
                  const top = y(v);
                  return (
                    <rect
                      key={key}
                      x={x0 + k * (barW + 2)}
                      y={top}
                      width={barW}
                      height={pad.top + plotH - top}
                      fill={color}
                      // Скруглён только верх — низ сидит на общей базовой линии.
                      rx={2}
                    >
                      <title>{`${d.date} — ${key === "submitted" ? "сдано" : "подтверждено"}: ${v}`}</title>
                    </rect>
                  );
                },
              )}
              {i % step === 0 && (
                <text
                  x={pad.left + i * slot + slot / 2}
                  y={h - 6}
                  textAnchor="middle"
                  fontSize={9}
                  fill={AXIS}
                >
                  {d.date.slice(5)}
                </text>
              )}
            </g>
          );
        })}
        <line
          x1={pad.left} x2={w - pad.right} y1={pad.top + plotH} y2={pad.top + plotH}
          stroke={GRID} strokeWidth={1}
        />
      </svg>
    </div>
  );
}

// ── динамика баллов студента ──────────────────────────────────────────────────

/**
 * Линия динамики. Подписывается только последняя точка: значение у каждой
 * превращает график в таблицу и перестаёт читаться.
 */
export function TrendLine({
  points,
  max,
}: {
  points: { label: string; value: number; hint?: string }[];
  max: number;
}) {
  if (!points.length) return <Empty>Нет оценённых работ.</Empty>;

  const w = 460;
  const h = 120;
  const pad = { left: 26, right: 34, top: 10, bottom: 22 };
  const plotW = w - pad.left - pad.right;
  const plotH = h - pad.top - pad.bottom;

  const top = Math.max(max, ...points.map((p) => p.value), 1);
  // Одна точка не образует линии: рисуем её по центру, а не в левом углу,
  // иначе первая сдача студента выглядит как обрыв графика.
  const x = (i: number) =>
    points.length === 1 ? pad.left + plotW / 2 : pad.left + (i / (points.length - 1)) * plotW;
  const y = (v: number) => pad.top + plotH - (v / top) * plotH;

  const path = points.map((p, i) => `${i ? "L" : "M"}${x(i)},${y(p.value)}`).join(" ");
  const last = points[points.length - 1];

  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full" role="img" aria-label="Динамика баллов">
      {[0, top / 2, top].map((t) => (
        <g key={t}>
          <line x1={pad.left} x2={w - pad.right} y1={y(t)} y2={y(t)} stroke={GRID} strokeWidth={1} />
          <text x={pad.left - 5} y={y(t) + 3} textAnchor="end" fontSize={9} fill={AXIS}>
            {Number.isInteger(t) ? t : t.toFixed(1)}
          </text>
        </g>
      ))}

      {points.length > 1 && (
        <path
          d={path}
          fill="none"
          stroke="var(--series-1)"
          strokeWidth={2}
          strokeLinejoin="round"
          strokeLinecap="round"
        />
      )}

      {points.map((p, i) => (
        <circle
          key={i}
          cx={x(i)}
          cy={y(p.value)}
          r={4}
          fill="var(--series-1)"
          // Кольцо цветом поверхности — точка остаётся читаемой там, где
          // пересекает линию или соседнюю точку.
          stroke="var(--surface)"
          strokeWidth={2}
        >
          <title>{p.hint ?? `${p.label}: ${p.value}`}</title>
        </circle>
      ))}

      <text
        x={x(points.length - 1) + 8}
        y={y(last.value) + 3}
        fontSize={11}
        fill="var(--text)"
        fontWeight={600}
      >
        {last.value}
      </text>

      {points.map((p, i) =>
        i % Math.ceil(points.length / 5) === 0 ? (
          <text key={i} x={x(i)} y={h - 6} textAnchor="middle" fontSize={9} fill={AXIS}>
            {p.label}
          </text>
        ) : null,
      )}
    </svg>
  );
}

// ── профиль по критериям ──────────────────────────────────────────────────────

/**
 * Радар по критериям рубрики.
 *
 * Оси — доли от максимума критерия, а не абсолютные баллы: критерии весят
 * от 1 до 4, и на абсолютной шкале самый дорогой всегда выглядел бы
 * сильнейшей стороной студента.
 *
 * Меньше трёх осей радар не выдерживает — двухосный «радар» вырождается в
 * отрезок и вводит в заблуждение. В этом случае рисуются полосы.
 */
export function RadarChart({
  items,
}: {
  items: { name: string; share: number }[];
}) {
  if (!items.length) return <Empty>Нет подтверждённых работ.</Empty>;

  if (items.length < 3) {
    return (
      <div className="flex flex-col gap-1.5">
        {items.map((it) => (
          <div key={it.name} className="flex items-center gap-2 text-xs">
            <span className="w-56 truncate" title={it.name}>
              {it.name}
            </span>
            <span className="h-2 flex-1 rounded" style={{ background: "var(--surface-2)" }}>
              <span
                className="block h-2 rounded"
                style={{ width: `${it.share * 100}%`, background: "var(--series-1)" }}
              />
            </span>
            <span className="w-10 text-right tabular-nums">{Math.round(it.share * 100)}%</span>
          </div>
        ))}
      </div>
    );
  }

  // Поле фигуры и поле подписей разведены. Подписи длинные («Обоснованная
  // приоритизация трёх критичных рисков»), и если считать их частью квадрата
  // фигуры, крайние обрезаются границей viewBox — что и происходило: у левой
  // оси пропадала первая буква. Поэтому viewBox начинается левее нуля.
  const size = 300;
  const padX = 66;
  const padY = 16;
  const c = size / 2;
  const r = size / 2 - 34;
  const n = items.length;
  const angle = (i: number) => (Math.PI * 2 * i) / n - Math.PI / 2;
  const point = (i: number, share: number) => [
    c + Math.cos(angle(i)) * r * share,
    c + Math.sin(angle(i)) * r * share,
  ];

  const polygon = items.map((it, i) => point(i, it.share).join(",")).join(" ");

  return (
    <div>
      <svg
        viewBox={`${-padX} ${-padY} ${size + padX * 2} ${size + padY * 2}`}
        className="mx-auto w-full max-w-[380px]"
        role="img"
        aria-label="Профиль по критериям"
      >
        {[0.25, 0.5, 0.75, 1].map((ring) => (
          <polygon
            key={ring}
            points={items.map((_, i) => point(i, ring).join(",")).join(" ")}
            fill="none"
            stroke={GRID}
            strokeWidth={1}
          />
        ))}
        {items.map((_, i) => (
          <line
            key={i}
            x1={c}
            y1={c}
            x2={point(i, 1)[0]}
            y2={point(i, 1)[1]}
            stroke={GRID}
            strokeWidth={1}
          />
        ))}

        <polygon
          points={polygon}
          fill="var(--series-1)"
          fillOpacity={0.12}
          stroke="var(--series-1)"
          strokeWidth={2}
          strokeLinejoin="round"
        />
        {items.map((it, i) => {
          const [px, py] = point(i, it.share);
          return (
            <circle
              key={i}
              cx={px}
              cy={py}
              r={4}
              fill="var(--series-1)"
              stroke="var(--surface)"
              strokeWidth={2}
            >
              <title>{`${it.name}: ${Math.round(it.share * 100)}% от максимума`}</title>
            </circle>
          );
        })}

        {/*
          На осях стоят номера, а названия целиком — в таблице под фигурой.
          Обрезка по 13 символам, которая была здесь раньше, превращала
          «Обоснованная приоритизация трёх критичных рисков» в «Обоснованная…»
          — то есть в подпись, не отличимую от соседней. Номер короче любого
          обрывка и ничего не теряет.
        */}
        {items.map((it, i) => {
          const [lx, ly] = point(i, 1.13);
          return (
            <g key={i}>
              <circle cx={lx} cy={ly} r={9} fill="var(--surface-2)" stroke={GRID} />
              <text
                x={lx}
                y={ly + 3.5}
                textAnchor="middle"
                fontSize={10}
                fontWeight={600}
                fill={AXIS}
              >
                {i + 1}
                <title>{`${it.name}: ${Math.round(it.share * 100)}%`}</title>
              </text>
            </g>
          );
        })}
      </svg>

      <table className="mt-2 w-full text-xs">
        <tbody>
          {items.map((it, i) => (
            <tr key={it.name} className="border-b last:border-0" style={{ borderColor: GRID }}>
              <td className="w-6 py-1 text-right tabular-nums muted">{i + 1}</td>
              <td className="py-1 pl-2">{it.name}</td>
              <td className="py-1 text-right tabular-nums">{Math.round(it.share * 100)}%</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── воронка потока ────────────────────────────────────────────────────────────

/**
 * Воронка: сколько работ дошло до каждого этапа.
 *
 * Ширина полосы — доля от первого шага. Числа рядом, а не только ширина:
 * на четырёх шагах глазом разницу в две работы не отличить, а методисту важно
 * именно «сколько потерялось», а не «примерно поменьше».
 */
export function Funnel({ steps }: { steps: { label: string; value: number }[] }) {
  if (!steps.length) return <Empty>Нет данных.</Empty>;
  const top = Math.max(steps[0].value, 1);

  return (
    <div className="flex flex-col gap-1.5">
      {steps.map((s, i) => {
        const share = s.value / top;
        const lost = i > 0 ? steps[i - 1].value - s.value : 0;
        return (
          <div key={s.label} className="text-xs">
            <div className="mb-0.5 flex justify-between">
              <span>{s.label}</span>
              <span className="tabular-nums">
                {s.value}
                {i > 0 && (
                  <span className="muted"> · {Math.round(share * 100)}%</span>
                )}
                {lost > 0 && (
                  <span style={{ color: "var(--text-dim)" }}> · −{lost}</span>
                )}
              </span>
            </div>
            <div className="h-3 w-full rounded" style={{ background: "var(--surface-2)" }}>
              <div
                className="h-3 rounded"
                style={{ width: `${Math.max(share * 100, 1)}%`, background: "var(--series-1)" }}
                title={`${s.label}: ${s.value}`}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ── ранжированные полосы ──────────────────────────────────────────────────────

/**
 * Горизонтальные полосы с подписью и вторым числом справа.
 *
 * Используется там, где важен порядок, а не абсолютная величина: какие
 * критерии проседают, как загружены ревьюеры. Значения — доли, поэтому
 * шкала всегда 0…100 % и полосы разных строк сравнимы между собой.
 */
export function RankedBars({
  items,
}: {
  items: { label: string; value: number; note?: string; tone?: "ok" | "warn" | "err" }[];
}) {
  if (!items.length) return <Empty>Нет данных.</Empty>;
  const color = (t?: string) =>
    t === "err" ? "var(--err)" : t === "warn" ? "var(--warn)" : t === "ok" ? "var(--ok)" : "var(--series-1)";

  return (
    <div className="flex flex-col gap-2">
      {items.map((it) => (
        <div key={it.label} className="text-xs">
          <div className="mb-0.5 flex items-baseline justify-between gap-3">
            <span className="min-w-0 flex-1 truncate" title={it.label}>
              {it.label}
            </span>
            <span className="tabular-nums">{Math.round(it.value * 100)}%</span>
            {it.note && <span className="muted whitespace-nowrap">{it.note}</span>}
          </div>
          <div className="h-2 w-full rounded" style={{ background: "var(--surface-2)" }}>
            <div
              className="h-2 rounded"
              style={{
                width: `${Math.max(Math.min(it.value, 1) * 100, 1)}%`,
                background: color(it.tone),
              }}
            />
          </div>
        </div>
      ))}
    </div>
  );
}

// ── распределение значений ────────────────────────────────────────────────────

/**
 * Одномерное распределение: точка на каждое наблюдение.
 *
 * Для десятка работ гистограмма врёт — в корзине одно-два значения, и форма
 * распределения дорисовывается воображением. Точки показывают ровно то, что
 * есть: сколько наблюдений и где они лежат.
 */
export function Strip({
  values,
  format = (v) => String(v),
  zero,
}: {
  values: number[];
  format?: (v: number) => string;
  zero?: boolean;
}) {
  if (!values.length) return <Empty>Нет данных.</Empty>;

  const min = Math.min(...values, zero ? 0 : Infinity);
  const max = Math.max(...values, zero ? 0 : -Infinity);
  const span = max - min || 1;
  const w = 460;
  const h = 54;
  const x = (v: number) => 18 + ((v - min) / span) * (w - 36);

  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full" role="img" aria-label="Распределение значений">
      <line x1={18} x2={w - 18} y1={26} y2={26} stroke={GRID} strokeWidth={1} />
      {zero && min < 0 && max > 0 && (
        <line x1={x(0)} x2={x(0)} y1={12} y2={40} stroke={GRID} strokeWidth={1} />
      )}
      {values.map((v, i) => (
        <circle
          key={i}
          cx={x(v)}
          cy={26}
          r={4}
          fill="var(--series-1)"
          fillOpacity={0.75}
          stroke="var(--surface)"
          strokeWidth={2}
        >
          <title>{format(v)}</title>
        </circle>
      ))}
      <text x={18} y={48} fontSize={9} fill={AXIS}>
        {format(min)}
      </text>
      <text x={w - 18} y={48} textAnchor="end" fontSize={9} fill={AXIS}>
        {format(max)}
      </text>
    </svg>
  );
}
