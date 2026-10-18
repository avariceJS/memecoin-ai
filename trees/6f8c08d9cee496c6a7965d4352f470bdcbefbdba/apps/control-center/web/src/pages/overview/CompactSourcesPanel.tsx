import type { Snapshot } from "../../api";
import shared from "../shared.module.css";
import styles from "./overview.module.css";

type CompactSourcesPanelProps = {
  snapshot: Snapshot;
};

export function CompactSourcesPanel({ snapshot }: CompactSourcesPanelProps) {
  const sources = Object.entries(snapshot.sources).sort(([a], [b]) => a.localeCompare(b));

  return (
    <section
      className={`${shared.panel} ${styles.compactSources}`}
      aria-labelledby="compact-sources-heading"
    >
      <h2 id="compact-sources-heading" className={shared.panelTitle}>
        Источники проекции
      </h2>
      <div className={styles.compactSourceGrid}>
        {sources.map(([id, stamp]) => (
          <article key={id} className={styles.compactSourceCard}>
            <p className={styles.compactSourceId}>{id}</p>
            <p className={styles.compactSourceMeta}>
              {stamp.evidenceDate} · {stamp.integrity}
            </p>
          </article>
        ))}
      </div>
    </section>
  );
}
