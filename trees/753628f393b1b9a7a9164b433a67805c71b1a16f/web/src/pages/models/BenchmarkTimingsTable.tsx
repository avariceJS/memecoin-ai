import { CopyButton } from "./CopyButton";
import type { BenchmarkRow } from "./benchmarkFilters";
import { formatTimingMs, shapeKey } from "./format";
import styles from "./models.module.css";

type BenchmarkTimingsTableProps = {
  benchmarks: BenchmarkRow[];
};

export function BenchmarkTimingsTable({ benchmarks }: BenchmarkTimingsTableProps) {
  return (
    <div className={styles.detail}>
      <h3 className={styles.detailTitle}>Все замеры · {benchmarks.length}</h3>
      <p className={styles.chartCaption}>
        Полный список без общей шкалы. Смешанные batch, status и метрики здесь не сравниваются.
      </p>
      <div
        className={styles.tableWrap}
        data-cc-scroll="benchmark-case-ids"
        tabIndex={0}
        aria-label="Идентификаторы замеров"
      >
        <table className={styles.dataTable}>
          <thead>
            <tr>
              <th scope="col">ID</th>
              <th scope="col">Архитектура</th>
              <th scope="col">Форма</th>
              <th scope="col">Устройство</th>
              <th scope="col">Пакет</th>
              <th scope="col">Инференс</th>
              <th scope="col">Прямой и обратный</th>
              <th scope="col">Статус</th>
              <th scope="col">Копия</th>
            </tr>
          </thead>
          <tbody>
            {benchmarks.map((row) => (
              <tr key={row.id}>
                <td className={styles.mono}>{row.id}</td>
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
                <td>
                  <CopyButton value={row.id} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
