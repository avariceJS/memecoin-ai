import type { Snapshot } from "../../api";
import styles from "./models.module.css";

type ModelsData = Extract<
  Snapshot["models"],
  { availability: "AVAILABLE" }
>["data"];

type Props = {
  data: ModelsData;
};

export function ModelsStatusAside({ data }: Props) {
  return (
    <aside className={styles.aside} aria-labelledby="models-aside-heading">
      <h2 id="models-aside-heading" className={styles.asideTitle}>
        Recorded model state
      </h2>
      <ul className={styles.asideList}>
        <li>
          <span className={styles.asideLabel}>activeModel</span>
          <span>{data.activeModel === null ? "не выбрана" : String(data.activeModel)}</span>
        </li>
        <li>
          <span className={styles.asideLabel}>marketBackboneSelected</span>
          <span>{data.marketBackboneSelected ? "true" : "false"}</span>
        </li>
        <li>
          <span className={styles.asideLabel}>runs</span>
          <span>{data.runs.length}</span>
        </li>
        <li>
          <span className={styles.asideLabel}>benchmarks</span>
          <span>{data.benchmarks.length}</span>
        </li>
      </ul>
      <p className={styles.panelHint} style={{ marginTop: 12 }}>
        Нет production backbone, cash model или GRU по умолчанию. Выбор победителя по этим
        runs недоступен.
      </p>
    </aside>
  );
}
