import type { Snapshot } from "../../api";
import styles from "./research.module.css";

type ResearchData = Extract<Snapshot["research"], { availability: "AVAILABLE" }>["data"];

type Props = {
  data: ResearchData;
};

export function ResearchKpiRow({ data }: Props) {
  return (
    <div className={styles.kpiRow} aria-label="Research counters">
      <article className={styles.kpi}>
        <p className={styles.kpiLabel}>Recorded research fits / fit budget</p>
        <p className={styles.kpiValue}>
          {data.recordedResearchFits}/{data.fitBudget}
        </p>
        <p className={styles.kpiSub}>бюджет протокола; не permission на launch</p>
      </article>
      <article className={styles.kpi}>
        <p className={styles.kpiLabel}>Engineering fits (отдельно)</p>
        <p className={styles.kpiValue}>{data.engineeringFits}</p>
        <p className={styles.kpiSub}>не суммировать с recorded/budget</p>
      </article>
      <article className={styles.kpi}>
        <p className={styles.kpiLabel}>Numeric checks (recorded)</p>
        <p className={styles.kpiValue}>{data.numericChecks}</p>
        <p className={styles.kpiSub}>счётчик gate; pass не даёт admission</p>
      </article>
      <article className={styles.kpi}>
        <p className={styles.kpiLabel}>Numeric blockers (recorded)</p>
        <p className={styles.kpiValue}>{data.numericBlockers}</p>
        <p className={styles.kpiSub}>остаток блокеров; не «готовность» в процентах</p>
      </article>
    </div>
  );
}
