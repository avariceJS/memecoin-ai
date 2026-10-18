import type { Snapshot } from "../../api";
import styles from "./system.module.css";

type RecordedCoreTestsPanelProps = {
  qualificationPane: Snapshot["sourceQualification"];
};

export function RecordedCoreTestsPanel({ qualificationPane }: RecordedCoreTestsPanelProps) {
  if (qualificationPane.availability !== "AVAILABLE") {
    return (
      <section className={styles.panel} aria-labelledby="recorded-core-tests-heading">
        <h3 id="recorded-core-tests-heading" className={styles.panelTitle}>
          Recorded core tests
        </h3>
        <p className={styles.caption}>
          sourceQualification недоступен — recorded stage tests не опубликованы.
        </p>
      </section>
    );
  }

  const { recordedBaseTestsPassed, recordedCombinedTestsPassed, recordedVerdict } =
    qualificationPane.data;

  return (
    <section className={styles.panel} aria-labelledby="recorded-core-tests-heading">
      <h3 id="recorded-core-tests-heading" className={styles.panelTitle}>
        Recorded core tests
      </h3>
      <p className={styles.recordedRow}>
        Base / combined:{" "}
        <span className={styles.recordedStrong}>
          {recordedBaseTestsPassed}/{recordedCombinedTestsPassed}
        </span>
        {" · "}
        verdict <span className={styles.recordedStrong}>{recordedVerdict}</span>
      </p>
      <p className={styles.caption}>
        Recorded stage из sourceQualification — зафиксированный прогон, не текущий процесс и не
        live health check.
      </p>
    </section>
  );
}
