import type { Snapshot } from "../../api";
import styles from "./models.module.css";

type ModelsData = Extract<Snapshot["models"], { availability: "AVAILABLE" }>["data"];

type ModelsStatusAsideProps = {
  data: ModelsData;
};

export function ModelsStatusAside({ data }: ModelsStatusAsideProps) {
  const scopes = [...new Set(data.runs.map((run) => run.scope))];
  return (
    <div className={styles.stateBlock}>
      <h3 className={styles.detailTitle}>Записанное состояние</h3>
      <dl className={styles.stateGrid}>
        <div>
          <dt>Активная модель</dt>
          <dd>{data.activeModel === null ? "не выбрана" : String(data.activeModel)}</dd>
        </div>
        <div>
          <dt>Рыночный backbone</dt>
          <dd>{data.marketBackboneSelected ? "выбран" : "не выбран"}</dd>
        </div>
        <div>
          <dt>Scope прогонов</dt>
          <dd>{scopes.length > 0 ? scopes.join(", ") : "нет"}</dd>
        </div>
        <div>
          <dt>Веса и checkpoints</dt>
          <dd>нет</dd>
        </div>
        <div>
          <dt>Торговый runtime</dt>
          <dd>нет</dd>
        </div>
      </dl>
    </div>
  );
}
