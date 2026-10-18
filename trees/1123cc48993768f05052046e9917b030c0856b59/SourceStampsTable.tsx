import type { Snapshot } from "../../api";
import { formatEpochNs } from "../calendar/timeZone";
import styles from "./sources.module.css";

type SourceStampsTableProps = {
  snapshot: Snapshot;
};

function formatRecordedAt(recordedAtNs: string | null): string {
  if (!recordedAtNs) return "время не опубликовано";
  return formatEpochNs(recordedAtNs, "UTC");
}

export function SourceStampsTable({ snapshot }: SourceStampsTableProps) {
  const rows = Object.entries(snapshot.sources).sort(([a], [b]) => a.localeCompare(b));

  return (
    <ul className={styles.stampList}>
      {rows.map(([id, stamp]) => (
        <li key={id} className={styles.stampCard}>
          <div className={styles.stampHead}>
            <span className={styles.stampId}>{id}</span>
            <span>{stamp.evidenceDate}</span>
            <span>{stamp.integrity}</span>
          </div>
          <div
            className={styles.idScroll}
            data-cc-scroll="stamp-hash"
            tabIndex={0}
            aria-label={`Хеш ${id}`}
          >
            {stamp.sha256}
          </div>
          <p className={styles.stampTime}>{formatRecordedAt(stamp.recordedAtNs)}</p>
        </li>
      ))}
    </ul>
  );
}
