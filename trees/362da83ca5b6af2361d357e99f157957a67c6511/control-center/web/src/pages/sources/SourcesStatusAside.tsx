import { QualificationBadge } from "../../components/status";
import type { SourceQualificationView } from "../overview/selectors";
import { formatClockAgeRangeSeconds } from "./format";
import styles from "./sources.module.css";

type SourcesStatusAsideProps = {
  qualification: SourceQualificationView;
  provenanceLabel: "REAL EVIDENCE" | "ENGINEERING" | "SYNTHETIC INPUT";
};

export function SourcesStatusAside({ qualification, provenanceLabel }: SourcesStatusAsideProps) {
  const {
    recordedVerdict,
    requests,
    accepted,
    blockers,
    clockAgeRangeSeconds,
    qualification: qualLabel,
    recordedBaseTestsPassed,
    recordedCombinedTestsPassed,
    sourceName,
    commitment,
  } = qualification;

  return (
    <aside className={styles.statusStack} aria-labelledby="sources-status-heading">
      <section className={styles.verdictPanel}>
        <h2 id="sources-status-heading" className={styles.verdictTitle}>
          Recorded qualification
        </h2>
        <p className={styles.verdictCode}>{recordedVerdict}</p>
        <p className={styles.metaLine}>
          <QualificationBadge kind={provenanceLabel} /> ·{" "}
          <span className={styles.metaStrong}>{qualLabel}</span>
        </p>
        <p className={styles.metaLine}>
          Источник probe: <span className={styles.metaStrong}>{sourceName}</span>
        </p>
        <p className={styles.metaLine}>
          Commitment <span className={styles.metaStrong}>{commitment}</span>
        </p>
        <p className={styles.probeNote}>
          Запросы probe {accepted}/{requests} — зафиксированный прогон D49, не индикатор
          текущего здоровья сети или RPC.
        </p>
      </section>

      <section className={styles.panel} aria-labelledby="sources-blockers-heading">
        <h3 id="sources-blockers-heading" className={styles.factTitle}>
          Blockers
        </h3>
        <ul className={styles.blockerList}>
          {blockers.map((blocker) => (
            <li key={blocker}>{blocker}</li>
          ))}
        </ul>
      </section>

      <section className={styles.panel} aria-labelledby="sources-clock-heading">
        <h3 id="sources-clock-heading" className={styles.factTitle}>
          Clock-age range
        </h3>
        <p className={styles.clockRange}>{formatClockAgeRangeSeconds(clockAgeRangeSeconds)}</p>
        <p className={styles.p95Note}>p95 не опубликован — в snapshot только диапазон clock-age.</p>
      </section>

      <section className={styles.panel} aria-labelledby="sources-tests-heading">
        <h3 id="sources-tests-heading" className={styles.factTitle}>
          Recorded tests stage
        </h3>
        <p className={styles.metaLine}>
          Base <span className={styles.metaStrong}>{recordedBaseTestsPassed}</span> / combined{" "}
          <span className={styles.metaStrong}>{recordedCombinedTestsPassed}</span>
        </p>
        <p className={styles.p95Note}>Стадия зафиксирована в snapshot, не live CI.</p>
      </section>
    </aside>
  );
}
