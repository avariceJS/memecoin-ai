import type { Snapshot } from "../../api";
import styles from "./research.module.css";

type ResearchData = Extract<Snapshot["research"], { availability: "AVAILABLE" }>["data"];

type Props = {
  data: ResearchData;
  qualification: string;
};

function formatBool(value: boolean): string {
  return value ? "true" : "false";
}

export function ProtocolBudgetPanel({ data, qualification }: Props) {
  return (
    <section className={styles.panel} aria-labelledby="research-protocol-heading">
      <h2 id="research-protocol-heading" className={styles.panelTitle}>
        Протокол и бюджет (recorded)
      </h2>
      <p className={styles.note}>
        Qualification <span className={styles.noteStrong}>{qualification}</span> — замороженный
        design, не operational admission.
      </p>
      <p className={styles.note}>
        Study id (spec): <span className={styles.noteStrong}>одна зафиксированная спецификация</span>
        , не единственная допустимая архитектура модели.
      </p>
      <p className={styles.studyId} aria-label="Study id">
        {data.specId}
      </p>

      <dl className={styles.metricGrid}>
        <div className={styles.metric}>
          <dt>Fit budget</dt>
          <dd>{data.fitBudget}</dd>
        </div>
        <div className={styles.metric}>
          <dt>Recorded research fits</dt>
          <dd>{data.recordedResearchFits}</dd>
        </div>
        <div className={styles.metric}>
          <dt>Engineering fits</dt>
          <dd>{data.engineeringFits}</dd>
          <p className={styles.metricSub}>отдельная ветка smoke, не budget+recorded</p>
        </div>
        <div className={styles.metric}>
          <dt>Recorded holdout releases</dt>
          <dd>{data.recordedHoldoutReleases}</dd>
          <p className={styles.metricSub}>
            не global exposures; 0 — не proof globally unseen
          </p>
        </div>
        <div className={styles.metric}>
          <dt>Numeric checks</dt>
          <dd>{data.numericChecks}</dd>
        </div>
        <div className={styles.metric}>
          <dt>Numeric blockers</dt>
          <dd>{data.numericBlockers}</dd>
        </div>
      </dl>

      <ul className={styles.flagList} aria-label="Recorded authorization flags">
        <li className={styles.flagRow}>
          <span className={styles.flagLabel}>recordedProtocolFrozen</span>
          <span className={styles.flagValue}>{formatBool(data.recordedProtocolFrozen)}</span>
        </li>
        <li className={styles.flagRow}>
          <span className={styles.flagLabel}>recordedResearchLaunchAuthorized</span>
          <span
            className={`${styles.flagValue} ${!data.recordedResearchLaunchAuthorized ? styles.flagValueFalse : ""}`}
          >
            {formatBool(data.recordedResearchLaunchAuthorized)}
          </span>
        </li>
        <li className={styles.flagRow}>
          <span className={styles.flagLabel}>recordedHoldoutReleaseAuthorized</span>
          <span
            className={`${styles.flagValue} ${!data.recordedHoldoutReleaseAuthorized ? styles.flagValueFalse : ""}`}
          >
            {formatBool(data.recordedHoldoutReleaseAuthorized)}
          </span>
        </li>
      </ul>
    </section>
  );
}
