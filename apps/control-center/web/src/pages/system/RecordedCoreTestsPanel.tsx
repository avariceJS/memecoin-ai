import type { Snapshot } from "../../api";
import { provenanceBadgeKind, QualificationBadge } from "../../components/status";
import { CodeLine } from "./CodeLine";
import styles from "./system.module.css";
import { recordedStageCaption } from "./viewModel";

type RecordedCoreTestsPanelProps = {
  qualificationPane: Snapshot["sourceQualification"];
};

export function RecordedCoreTestsPanel({ qualificationPane }: RecordedCoreTestsPanelProps) {
  if (qualificationPane.availability !== "AVAILABLE") {
    return (
      <section className={styles.panel} aria-labelledby="recorded-core-tests-heading">
        <div className={styles.panelHead}>
          <h3 id="recorded-core-tests-heading" className={styles.panelTitle}>
            Записанные тесты
          </h3>
          <QualificationBadge kind="UNAVAILABLE" />
        </div>
        <p className={styles.caption}>
          sourceQualification недоступен. Записанный этап не опубликован и не является текущим
          процессом.
        </p>
      </section>
    );
  }

  const { recordedBaseTestsPassed, recordedCombinedTestsPassed, recordedVerdict } =
    qualificationPane.data;

  return (
    <section className={styles.panel} aria-labelledby="recorded-core-tests-heading">
      <div className={styles.panelHead}>
        <h3 id="recorded-core-tests-heading" className={styles.panelTitle}>
          Записанные тесты
        </h3>
        <QualificationBadge kind={provenanceBadgeKind(qualificationPane.provenance)} />
      </div>
      <p className={styles.metric}>
        {recordedBaseTestsPassed}/{recordedCombinedTestsPassed}
      </p>
      <p className={styles.caption}>{recordedStageCaption(qualificationPane.qualification)}</p>
      <CodeLine scrollId="recorded-verdict" label="Вердикт записанного прогона" value={recordedVerdict} />
    </section>
  );
}
