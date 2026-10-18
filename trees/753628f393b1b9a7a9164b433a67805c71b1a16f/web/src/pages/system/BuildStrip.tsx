import type { Snapshot } from "../../api";
import styles from "./system.module.css";

type BuildStripProps = {
  snapshot: Snapshot;
};

export function BuildStrip({ snapshot }: BuildStripProps) {
  const pins = Object.values(snapshot.sources).sort((left, right) =>
    left.artifactId.localeCompare(right.artifactId),
  );
  const dates = [...new Set(pins.map((pin) => pin.evidenceDate))];

  return (
    <section className={styles.pinBar} aria-labelledby="build-strip-heading">
      <div className={styles.pinMeta}>
        <h3 id="build-strip-heading" className={styles.pinTitle}>
          Сборка проекции
        </h3>
        <p className={styles.caption}>
          baseline <span className={styles.captionStrong}>{snapshot.coreBaselineCommit}</span>
          {dates.length > 0 ? (
            <>
              {" · "}
              дата записи <span className={styles.captionStrong}>{dates.join(", ")}</span>
            </>
          ) : null}
        </p>
      </div>
      {pins.length > 0 ? (
        <ul
          className={styles.pinScroll}
          data-cc-scroll="source-pins"
          tabIndex={0}
          aria-label="Хеши источников"
        >
          {pins.map((pin) => (
            <li key={pin.artifactId} className={styles.pinItem}>
              <span className={styles.pinId}>{pin.artifactId}</span>
              <span className={styles.pinHash}>{pin.sha256}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className={styles.caption}>Пины источников не опубликованы.</p>
      )}
    </section>
  );
}
