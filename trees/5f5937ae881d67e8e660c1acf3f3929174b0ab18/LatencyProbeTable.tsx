import type { SourceQualificationView } from "../overview/selectors";
import { formatProbeSeconds } from "./format";
import styles from "./sources.module.css";

type LatencyProbeTableProps = {
  qualification: SourceQualificationView;
};

export function LatencyProbeTable({ qualification }: LatencyProbeTableProps) {
  const { latencySeconds, sourceName, commitment } = qualification;
  const max = Math.max(...latencySeconds, 0.001);

  return (
    <section className={styles.panel} aria-labelledby="latency-table-heading">
      <h2 id="latency-table-heading" className={styles.panelTitle}>
        Historical probe latency · {sourceName}
      </h2>
      <p className={styles.panelHint}>
        Шесть записей в секундах · commitment {commitment} · не текущий RPC health.
      </p>
      <div className={styles.tableWrap}>
        <table className={styles.dataTable}>
          <thead>
            <tr>
              <th scope="col">#</th>
              <th scope="col">Latency</th>
              <th scope="col">Единица</th>
              <th scope="col">Относительно max</th>
            </tr>
          </thead>
          <tbody>
            {latencySeconds.map((seconds, index) => {
              const widthPct = Math.max(4, (seconds / max) * 100);
              return (
                <tr key={index}>
                  <td className={styles.muted}>{index + 1}</td>
                  <td className={styles.metaStrong}>{formatProbeSeconds(seconds)}</td>
                  <td>с</td>
                  <td className={styles.latencyBarCell}>
                    <span className={styles.muted}>{widthPct.toFixed(0)}%</span>
                    <div className={styles.barTrack} aria-hidden="true">
                      <div className={styles.barFill} style={{ width: `${widthPct}%` }} />
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
