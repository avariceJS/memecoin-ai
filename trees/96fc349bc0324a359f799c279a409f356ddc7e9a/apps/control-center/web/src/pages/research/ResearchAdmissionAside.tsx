import { Link } from "react-router";
import { QualificationBadge } from "../../components/status";
import type { Snapshot } from "../../api";
import styles from "./research.module.css";

type ResearchData = Extract<Snapshot["research"], { availability: "AVAILABLE" }>["data"];

type Props = {
  data: ResearchData;
};

export function ResearchAdmissionAside({ data }: Props) {
  const s = data.sampling;

  return (
    <aside className={styles.asideStack} aria-label="Admission and gates">
      <section className={styles.gateBlock} aria-labelledby="research-admission-heading">
        <h2 id="research-admission-heading" className={styles.gateTitle}>
          Admission и holdout
        </h2>
        <div className={styles.gateRow}>
          <span>admission</span>
          <QualificationBadge kind="LOCKED" />
        </div>
        <div className={styles.gateRow}>
          <span>holdout</span>
          <QualificationBadge kind="PROTECTED" />
        </div>
        <div className={styles.gateRow}>
          <span>globallyUnseenHoldout</span>
          <span className={styles.gateCode}>{data.globallyUnseenHoldout}</span>
        </div>
        <p className={styles.note}>
          NOT_CERTIFIED — нет UI-доказательства globally unseen; numeric gates отдельно.
        </p>
      </section>

      <section className={styles.panel} aria-labelledby="research-reviews-heading">
        <h2 id="research-reviews-heading" className={styles.panelTitle}>
          missingReviews ({data.missingReviews.length})
        </h2>
        <ul className={styles.reviewList}>
          {data.missingReviews.map((id) => (
            <li key={id}>{id}</li>
          ))}
        </ul>
      </section>

      <section className={styles.samplingBrief} aria-labelledby="research-sampling-heading">
        <h2 id="research-sampling-heading" className={styles.panelTitle}>
          Sampling (кратко)
        </h2>
        <p className={styles.note}>
          Полная сетка planned/dev/reserved/admitted — на календаре; здесь без второй истины.
        </p>
        <div className={styles.samplingCounts}>
          <span>
            planned<strong>{s.plannedWindows}</strong>
          </span>
          <span>
            development<strong>{s.developmentWindows}</strong>
          </span>
          <span>
            reserved<strong>{s.reservedWindows}</strong>
          </span>
          <span>
            admitted<strong>{s.admittedWindows}</strong>
          </span>
        </div>
        <Link to="/calendar" className={styles.calendarLink}>
          Открыть календарь →
        </Link>
      </section>

      <section className={styles.unavailableBlock} aria-labelledby="research-export-heading">
        <h2 id="research-export-heading" className={styles.unavailableTitle}>
          Ledger / event export
        </h2>
        <p className={styles.note}>
          <QualificationBadge kind="UNAVAILABLE" /> Нет approved read contract в snapshot.
          Запрос интерфейса: proposal{" "}
          <span className={styles.noteStrong}>CC-P001 — безопасный operational metadata export</span>
          (не raw tree, не запуск из UI).
        </p>
      </section>
    </aside>
  );
}
