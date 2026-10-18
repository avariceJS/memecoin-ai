import type { SnapshotResponse } from "../api";
import { primaryEvidenceDate, truncateId } from "../pages/snapshotFacts";
import styles from "./Header.module.css";

type HeaderProps = {
  title: string;
  snapshotResponse: SnapshotResponse | undefined;
};

export function Header({ title, snapshotResponse }: HeaderProps) {
  const snapshot = snapshotResponse?.data;
  const evidenceDate = snapshot ? primaryEvidenceDate(snapshot) : null;
  const snapshotId = snapshotResponse?.snapshotId;

  return (
    <header className={styles.header}>
      <div className={styles.titleBlock}>
        <h1 className={styles.pageTitle}>{title}</h1>
        {snapshot && evidenceDate ? (
          <p className={styles.meta}>
            Запись evidence: {evidenceDate}
            {snapshotId ? (
              <>
                {" "}
                · snapshot{" "}
                <span className={styles.metaMono} title={snapshotId}>
                  {truncateId(snapshotId)}
                </span>
              </>
            ) : null}
          </p>
        ) : (
          <p className={styles.meta}>Проекция не загружена</p>
        )}
      </div>
      <div className={styles.right}>
        {snapshot ? (
          <span className={styles.badge} aria-label="RECORDED EVIDENCE">
            RECORDED EVIDENCE
          </span>
        ) : null}
      </div>
    </header>
  );
}
