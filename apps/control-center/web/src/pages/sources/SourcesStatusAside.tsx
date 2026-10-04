import { useId, useState } from "react";
import { QualificationBadge, type ProvenanceBadgeKind } from "../../components/status";
import type { SourceQualificationView } from "../overview/selectors";
import styles from "./sources.module.css";
import { blockerLabel } from "./viewModel";

type SourcesStatusAsideProps = {
  qualification: SourceQualificationView;
  provenance: ProvenanceBadgeKind;
};

export function SourcesStatusAside({ qualification, provenance }: SourcesStatusAsideProps) {
  const titleId = useId();
  const panelId = useId();
  const [open, setOpen] = useState(false);
  const { blockers } = qualification;

  return (
    <section className={styles.panel} data-cc-region="source-blockers" aria-labelledby={titleId}>
      <div className={styles.panelHead}>
        <h2 id={titleId} className={styles.panelTitle}>
          Блокеры
        </h2>
        <QualificationBadge kind={provenance} />
      </div>
      <p className={styles.summary}>Записано кодов: {blockers.length}.</p>
      {blockers.length === 0 ? (
        <p className={styles.summary}>Записанных блокеров нет.</p>
      ) : (
        <ul className={styles.blockerList}>
          {blockers.map((code) => (
            <li key={code} className={styles.blockerItem}>
              {blockerLabel(code)}
            </li>
          ))}
        </ul>
      )}
      {blockers.length > 0 ? (
        <div className={styles.disclosure}>
          <button
            type="button"
            className={styles.textButton}
            aria-expanded={open}
            aria-controls={open ? panelId : undefined}
            onClick={() => setOpen((current) => !current)}
          >
            Сырой код
          </button>
          {open ? (
            <ul
              id={panelId}
              className={styles.codeScroll}
              data-cc-scroll="source-blocker-codes"
              tabIndex={0}
              aria-label="Сырые коды блокеров"
            >
              {blockers.map((code) => (
                <li key={code}>{code}</li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
