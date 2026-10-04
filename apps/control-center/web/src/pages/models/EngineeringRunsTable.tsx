import type { Snapshot } from "../../api";
import { CopyButton } from "./CopyButton";
import { formatCount } from "./format";
import styles from "./models.module.css";

type RunRow = Extract<Snapshot["models"], { availability: "AVAILABLE" }>["data"]["runs"][number];

type EngineeringRunsTableProps = {
  runs: RunRow[];
};

export function EngineeringRunsTable({ runs }: EngineeringRunsTableProps) {
  return (
    <div className={styles.detail}>
      <h3 className={styles.detailTitle}>Прогоны · {runs.length}</h3>
      <p className={styles.chartCaption}>
        Записанный список. REAL dataset — метка данных прогона, не рыночное качество. Выбора модели нет.
      </p>
      <div
        className={styles.tableWrap}
        data-cc-scroll="model-run-ids"
        tabIndex={0}
        aria-label="Идентификаторы прогонов"
      >
        <table className={styles.dataTable}>
          <thead>
            <tr>
              <th scope="col">ID</th>
              <th scope="col">Архитектура</th>
              <th scope="col">Датасет</th>
              <th scope="col">Сид</th>
              <th scope="col">Эпохи</th>
              <th scope="col">Параметры</th>
              <th scope="col">Выбранная эпоха</th>
              <th scope="col">Копия</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr key={run.id}>
                <td className={styles.mono}>{run.id}</td>
                <td>{run.modelKind}</td>
                <td>
                  {run.datasetKind === "REAL" ? (
                    <span className={styles.datasetReal}>REAL dataset</span>
                  ) : (
                    <span className={styles.datasetSynthetic}>SYNTHETIC</span>
                  )}
                </td>
                <td>{run.seed}</td>
                <td>{run.epochs}</td>
                <td>{formatCount(run.parameters)}</td>
                <td>{run.selectedEpoch}</td>
                <td>
                  <CopyButton value={run.id} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
