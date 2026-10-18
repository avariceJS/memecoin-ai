import { useMemo, useState } from "react";
import type { Snapshot } from "../../api";
import {
  filterBenchmarks,
  minMeasuredInference,
  timingsComparable,
  uniqueDevices,
  uniqueShapes,
} from "./benchmarkFilters";
import { formatTimingMs, shapeKey } from "./format";
import styles from "./models.module.css";

type BenchmarkRow = Extract<
  Snapshot["models"],
  { availability: "AVAILABLE" }
>["data"]["benchmarks"][number];

type Props = {
  benchmarks: BenchmarkRow[];
};

function comparisonLabel(
  row: BenchmarkRow,
  comparable: boolean,
  minInf: number | null,
): string {
  if (!comparable) return "несопоставимо";
  if (row.medianInferenceMs === null) return "не измерено";
  if (minInf === null) return "не измерено";
  if (row.medianInferenceMs === minInf) return "мин. в группе";
  const ratio =
    ((row.medianInferenceMs - minInf) / minInf) * 100;
  return `+${ratio.toFixed(0)}% к min inf.`;
}

export function BenchmarkTimingsTable({ benchmarks }: Props) {
  const devices = useMemo(() => uniqueDevices(benchmarks), [benchmarks]);
  const shapes = useMemo(() => uniqueShapes(benchmarks), [benchmarks]);
  const [device, setDevice] = useState<string>("all");
  const [shape, setShape] = useState<string>("all");

  const filtered = useMemo(
    () => filterBenchmarks(benchmarks, device, shape),
    [benchmarks, device, shape],
  );
  const comparable = timingsComparable(filtered);
  const minInf = comparable ? minMeasuredInference(filtered) : null;

  return (
    <section className={styles.panel} aria-labelledby="bench-table-heading">
      <h2 id="bench-table-heading" className={styles.panelTitle}>
        Benchmark timings · {filtered.length}/{benchmarks.length}
      </h2>
      <p className={styles.panelHint}>
        inputKind SYNTHETIC · сравнение median inference только при одной форме (width×context)
        и одном device. Разный batch внутри группы не делает строки одним экспериментом.
      </p>

      <div className={styles.filterRow}>
        <div className={styles.filterGroup}>
          <label className={styles.filterLabel} htmlFor="bench-device-filter">
            Device
          </label>
          <select
            id="bench-device-filter"
            className={styles.filterSelect}
            value={device}
            onChange={(e) => setDevice(e.target.value)}
          >
            <option value="all">Все</option>
            {devices.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        </div>
        <div className={styles.filterGroup}>
          <label className={styles.filterLabel} htmlFor="bench-shape-filter">
            Shape (width×context)
          </label>
          <select
            id="bench-shape-filter"
            className={styles.filterSelect}
            value={shape}
            onChange={(e) => setShape(e.target.value)}
          >
            <option value="all">Все</option>
            {shapes.map((s) => (
              <option key={s.label} value={s.label}>
                {s.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      {!comparable && filtered.length > 0 ? (
        <p className={styles.compareWarn} role="status">
          Сравнение таймингов между строками: несопоставимо — сузьте device и shape.
        </p>
      ) : null}

      <div className={styles.tableWrap}>
        <table className={styles.dataTable}>
          <thead>
            <tr>
              <th scope="col">modelKind</th>
              <th scope="col">shape</th>
              <th scope="col">device</th>
              <th scope="col">batch</th>
              <th scope="col">median inf.</th>
              <th scope="col">median f+b</th>
              <th scope="col">status</th>
              <th scope="col">сравнение</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((row) => (
              <tr key={row.id} tabIndex={0} aria-label={`benchmark ${row.id}`}>
                <td>{row.modelKind}</td>
                <td>{shapeKey(row.width, row.context)}</td>
                <td>{row.device}</td>
                <td>{row.batchSize}</td>
                <td className={row.medianInferenceMs === null ? styles.muted : undefined}>
                  {formatTimingMs(row.medianInferenceMs)}
                </td>
                <td className={row.medianForwardBackwardMs === null ? styles.muted : undefined}>
                  {formatTimingMs(row.medianForwardBackwardMs)}
                </td>
                <td>{row.status}</td>
                <td className={!comparable ? styles.muted : undefined}>
                  {comparisonLabel(row, comparable, minInf)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
