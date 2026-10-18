import { QualificationBadge } from "../../components/status";
import type { SourceQualificationView } from "./selectors";
import styles from "./overview.module.css";

type QualificationStatusPanelProps = {
  qualification: SourceQualificationView;
  systemReason: string | null;
  admission: string | null;
};

function formatRangeSeconds(range: number[]): string {
  if (range.length < 2) return "—";
  const lo = Math.min(range[0]!, range[1]!);
  const hi = Math.max(range[0]!, range[1]!);
  return `${lo.toFixed(3)}–${hi.toFixed(3)} с`;
}

export function QualificationStatusPanel({
  qualification,
  systemReason,
  admission,
}: QualificationStatusPanelProps) {
  const {
    recordedVerdict,
    requests,
    accepted,
    blockers,
    clockAgeRangeSeconds,
    qualification: qualLabel,
  } = qualification;

  return (
    <aside className={styles.statusStack} aria-labelledby="qual-status-heading">
      <section className={styles.verdictPanel}>
        <h2 id="qual-status-heading" className={styles.verdictTitle}>
          Recorded verdict
        </h2>
        <p className={styles.verdictCode}>{recordedVerdict}</p>
        <p className={styles.metaLine}>
          Qualification <span className={styles.metaStrong}>{qualLabel}</span>
        </p>
        <p className={styles.probeNote}>
          Запросы probe {accepted}/{requests} — зафиксированный прогон, не индикатор live RPC.
        </p>
        {admission ? (
          <p className={styles.metaLine}>
            Admission <QualificationBadge kind="LOCKED" />
          </p>
        ) : null}
        <p className={styles.systemNote}>
          Текущий runtime неизвестен: pane system {systemReason ?? "UNAVAILABLE"} — adapter не
          подтверждает core online.
        </p>
      </section>

      <section className={styles.statusPanel} aria-labelledby="blockers-heading">
        <h3 id="blockers-heading" className={styles.factTitle}>
          Blockers
        </h3>
        <ul className={styles.blockerList}>
          {blockers.map((blocker) => (
            <li key={blocker}>{blocker}</li>
          ))}
        </ul>
      </section>

      <section className={styles.statusPanel} aria-labelledby="clock-age-heading">
        <h3 id="clock-age-heading" className={styles.factTitle}>
          Clock-age range
        </h3>
        <p className={styles.clockRange}>{formatRangeSeconds(clockAgeRangeSeconds)}</p>
        <p className={styles.p95Note}>p95 не опубликован — в snapshot только диапазон clock-age.</p>
      </section>
    </aside>
  );
}
