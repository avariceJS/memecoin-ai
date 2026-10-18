import type { SourceQualificationView } from "./selectors";
import styles from "./overview.module.css";

type LatencyEvidencePanelProps = {
  qualification: SourceQualificationView;
};

function formatSeconds(value: number): string {
  if (value >= 1) return value.toFixed(3);
  return value.toFixed(6).replace(/0+$/, "").replace(/\.$/, ".0");
}

export function LatencyEvidencePanel({ qualification }: LatencyEvidencePanelProps) {
  const { latencySeconds, sourceName, commitment } = qualification;
  const max = Math.max(...latencySeconds, 0.001);

  return (
    <section className={styles.evidencePanel} aria-labelledby="latency-evidence-heading">
      <div className={styles.panelHead}>
        <h2 id="latency-evidence-heading" className={styles.panelTitle}>
          Recorded latency · {sourceName}
        </h2>
        <p className={styles.panelHint}>
          Исторический probe, не текущий RPC · commitment {commitment}
        </p>
      </div>
      <div
        className={styles.latencyChart}
        role="img"
        aria-label={`Шесть значений latency в секундах: ${latencySeconds.map(formatSeconds).join(", ")}`}
      >
        {latencySeconds.map((seconds, index) => {
          const heightPct = Math.max(6, (seconds / max) * 100);
          return (
            <div key={index} className={styles.latencyCol}>
              <div className={styles.latencyBarTrack}>
                <div className={styles.latencyBar} style={{ height: `${heightPct}%` }} />
              </div>
              <p className={styles.latencyValue}>{formatSeconds(seconds)}</p>
              <p className={styles.latencyUnit}>с</p>
              <p className={styles.latencyIndex}>#{index + 1}</p>
            </div>
          );
        })}
      </div>
    </section>
  );
}
