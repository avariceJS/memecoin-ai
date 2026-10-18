import type { Snapshot } from "../../api";
import { formatCount } from "./format";
import styles from "./models.module.css";

type RunRow = Extract<
  Snapshot["models"],
  { availability: "AVAILABLE" }
>["data"]["runs"][number];

type Props = {
  runs: RunRow[];
};

export function EngineeringRunsTable({ runs }: Props) {
  return (
    <section className={styles.panel} aria-labelledby="runs-table-heading">
      <h2 id="runs-table-heading" className={styles.panelTitle}>
        Engineering smoke runs · {runs.length}
      </h2>
      <p className={styles.panelHint}>
        Записанные прогоны без выбора победителя и без launch/resume/promote. REAL dataset —
        отдельная метка, не рыночное качество.
      </p>
      <div className={styles.tableWrap}>
        <table className={styles.dataTable}>
          <thead>
            <tr>
              <th scope="col">ID</th>
              <th scope="col">modelKind</th>
              <th scope="col">dataset</th>
              <th scope="col">seed</th>
              <th scope="col">epochs</th>
              <th scope="col">params</th>
              <th scope="col">selectedEpoch</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr key={run.id} tabIndex={0} aria-label={`run ${run.id}`}>
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
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
