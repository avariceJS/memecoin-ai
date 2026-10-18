import { Panel } from "../../components";
import { formatClockAgeRangeSeconds, formatProbeSeconds } from "./format";
import type { SourceQualificationView } from "./selectors";
import styles from "./overview.module.css";

type LatencyEvidencePanelProps = {
  qualification: SourceQualificationView | null;
};

export function LatencyEvidencePanel({ qualification }: LatencyEvidencePanelProps) {
  if (!qualification) {
    return (
      <Panel title="Latency probe" density="compact">
        <p className={styles.note}>Квалификация источника в snapshot недоступна.</p>
      </Panel>
    );
  }

  const { latencySeconds, clockAgeRangeSeconds, recordedBaseTestsPassed, recordedCombinedTestsPassed } =
    qualification;
  const max = Math.max(...latencySeconds, 0.001);

  return (
    <Panel title="Latency probe" density="compact">
      <p className={styles.note}>Исторический probe, секунды, не текущий RPC.</p>
      <ul
        className={styles.latencyList}
        aria-label={`Шесть значений latency в секундах: ${latencySeconds.map(formatProbeSeconds).join(", ")}`}
      >
        {latencySeconds.map((seconds, index) => (
          <li key={index} className={styles.latencyRow}>
            <span className={styles.latencyIndex}>#{index + 1}</span>
            <span className={styles.latencyTrack} aria-hidden="true">
              <span className={styles.latencyBar} style={{ width: `${Math.max(6, (seconds / max) * 100)}%` }} />
            </span>
            <span className={styles.latencyValue}>{formatProbeSeconds(seconds)}</span>
            <span className={styles.latencyUnit}>с</span>
          </li>
        ))}
      </ul>
      <p className={styles.note}>
        Base <strong>{recordedBaseTestsPassed}</strong> / combined <strong>{recordedCombinedTestsPassed}</strong>
        . Clock-age {formatClockAgeRangeSeconds(clockAgeRangeSeconds)}. p95 не опубликован — в snapshot
        только диапазон clock-age.
      </p>
    </Panel>
  );
}
