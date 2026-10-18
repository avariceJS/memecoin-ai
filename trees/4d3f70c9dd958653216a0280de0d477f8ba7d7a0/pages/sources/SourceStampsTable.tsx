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
    <section className={styles.panel} aria-labelledby="source-stamps-heading">
      <h2 id="source-stamps-heading" className={styles.panelTitle}>
        Source stamps · provenance
      </h2>
      <p className={styles.panelHint}>
        PINNED_BYTES и evidenceDate — зафиксированная проекция, не live feed.
      </p>
      <div className={styles.tableWrap}>
        <table className={styles.dataTable}>
          <thead>
            <tr>
              <th scope="col">ID</th>
              <th scope="col">evidenceDate</th>
              <th scope="col">integrity</th>
              <th scope="col">recordedAtNs</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(([id, stamp]) => (
              <tr key={id}>
                <td className={styles.stampId}>{id}</td>
                <td>{stamp.evidenceDate}</td>
                <td>{stamp.integrity}</td>
                <td className={stamp.recordedAtNs ? styles.mono : styles.muted}>
                  {formatRecordedAt(stamp.recordedAtNs)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
