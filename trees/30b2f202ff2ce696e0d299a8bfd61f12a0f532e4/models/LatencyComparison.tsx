import {
  comparisonError,
  timingsComparable,
  type BenchmarkRow,
  type ComparisonMode,
  type TimingMetric,
} from "./benchmarkFilters";
import { formatTimingMs, metricLabel, shapeKey } from "./format";
import styles from "./models.module.css";

type LatencyComparisonProps = {
  rows: BenchmarkRow[];
  mode: ComparisonMode;
  metric: TimingMetric;
  title: string;
};

function axisLabel(row: BenchmarkRow, mode: ComparisonMode): string {
  return mode === "device" ? row.device : row.modelKind;
}

function toneOf(row: BenchmarkRow, mode: ComparisonMode): string {
  if (mode === "device") {
    if (row.device === "cpu" || row.device === "mps") return row.device;
    return "other";
  }
  if (
    row.modelKind === "linear" ||
    row.modelKind === "mlp" ||
    row.modelKind === "gru" ||
    row.modelKind === "tcn" ||
    row.modelKind === "transformer"
  ) {
    return row.modelKind;
  }
  return "other";
}

export function LatencyComparison({ rows, mode, metric, title }: LatencyComparisonProps) {
  const error = comparisonError(rows, mode, metric);
  const comparable = timingsComparable(rows, mode, metric);
  const ordered = [...rows].sort((left, right) => {
    const byAxis = axisLabel(left, mode).localeCompare(axisLabel(right, mode));
    return byAxis || left.id.localeCompare(right.id);
  });
  const measured = ordered.map((row) => row[metric]).filter((value): value is number => value !== null);
  const max = measured.length > 0 ? Math.max(...measured) : 0;

  return (
    <div className={styles.chartBlock}>
      <h3 className={styles.chartTitle}>{title}</h3>
      <p className={styles.chartCaption}>{caption(rows, mode, metric)}</p>
      {error ? (
        <div role="status" className={styles.compareError}>
          <p>{error}</p>
          {ordered.length > 0 ? (
            <ul className={styles.rawList}>
              {ordered.map((row) => (
                <li key={row.id}>
                  {axisLabel(row, mode)} · batch {row.batchSize} · status {row.status} ·{" "}
                  {formatTimingMs(row[metric])}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
      {comparable ? (
        <>
          {ordered.length < 2 ? <p className={styles.chartCaption}>Одна запись. Сравнивать не с чем.</p> : null}
          <ul className={styles.barList} aria-label={title}>
            {ordered.map((row) => {
              const value = row[metric];
              const width = value === null || max <= 0 ? 0 : (value / max) * 100;
              return (
                <li key={row.id} className={styles.barRow}>
                  <span className={styles.barName} title={axisLabel(row, mode)}>
                    {axisLabel(row, mode)}
                  </span>
                  <span className={styles.track} aria-hidden="true">
                    <span className={styles.fill} data-tone={toneOf(row, mode)} style={{ width: `${width}%` }} />
                  </span>
                  <span className={styles.barValue}>{formatTimingMs(value)}</span>
                </li>
              );
            })}
          </ul>
          <div className={styles.scale} aria-hidden="true">
            <span />
            <span className={styles.scaleTrack}>
              <span>0</span>
              <span>{formatTimingMs(max)}</span>
            </span>
            <span>мс</span>
          </div>
        </>
      ) : null}
    </div>
  );
}

function sameValue(rows: readonly BenchmarkRow[], read: (row: BenchmarkRow) => string | number): boolean {
  const first = rows[0];
  if (!first) return false;
  const expected = read(first);
  return rows.every((row) => read(row) === expected);
}

function caption(rows: readonly BenchmarkRow[], mode: ComparisonMode, metric: TimingMetric): string {
  if (rows.length === 0) return "Группа пуста. Порядок по имени, шкала не строится.";
  const first = rows[0]!;
  const shape = sameValue(rows, (row) => shapeKey(row.width, row.context))
    ? shapeKey(first.width, first.context)
    : "разная форма";
  const batch = sameValue(rows, (row) => row.batchSize) ? `batch ${first.batchSize}` : "разный batch";
  const status = sameValue(rows, (row) => row.status) ? `status ${first.status}` : "разный status";
  let fixed = "разные device";
  if (mode === "device") {
    fixed = sameValue(rows, (row) => row.modelKind) ? first.modelKind : "разные модели";
  } else if (sameValue(rows, (row) => row.device)) {
    fixed = first.device;
  }
  return `${fixed} · ${shape} · ${batch} · ${metricLabel(metric)} · ${status}. Порядок по имени. Большее время — больший замер.`;
}
