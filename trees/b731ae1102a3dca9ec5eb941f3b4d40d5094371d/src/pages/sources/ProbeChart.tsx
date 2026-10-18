import { useId } from "react";
import { QualificationBadge, type ProvenanceBadgeKind } from "../../components/status";
import type { SourceQualificationView } from "../overview/selectors";
import { formatClockAgeRangeSeconds, formatProbeSeconds } from "./format";
import styles from "./sources.module.css";
import { probeScale } from "./viewModel";

type ProbeChartProps = {
  qualification: SourceQualificationView;
  provenance: ProvenanceBadgeKind;
};

export function ProbeChart({ qualification, provenance }: ProbeChartProps) {
  const titleId = useId();
  const {
    latencySeconds,
    sourceName,
    commitment,
    clockAgeRangeSeconds,
    recordedBaseTestsPassed,
    recordedCombinedTestsPassed,
  } = qualification;
  const { max, scalable } = probeScale(latencySeconds);

  return (
    <section className={styles.panel} aria-labelledby={titleId}>
      <div className={styles.panelHead}>
        <h2 id={titleId} className={styles.panelTitle}>
          Замеры latency
        </h2>
        <QualificationBadge kind={provenance} />
      </div>
      <p className={styles.summary}>
        {sourceName} · commitment {commitment} · секунды. Порядок — номер опубликованного замера, не ось
        времени. Объём не опубликован.
      </p>
      {latencySeconds.length === 0 ? (
        <p className={styles.summary}>Опубликованных замеров нет.</p>
      ) : null}
      {latencySeconds.length > 0 && !scalable ? (
        <p className={styles.summary}>Шкала не строится: в ряде есть значение вне неотрицательных секунд.</p>
      ) : null}
      {latencySeconds.length > 0 ? (
        <div className={styles.plot}>
          <ul className={styles.barList} aria-label="Опубликованные замеры latency">
            {latencySeconds.map((seconds, index) => {
              const width = scalable ? (seconds / max) * 100 : 0;
              return (
                <li key={index} className={styles.barRow}>
                  <span className={styles.barIndex}>{index + 1}</span>
                  <span className={styles.track} aria-hidden="true">
                    {scalable ? (
                      <span
                        className={styles.fill}
                        style={{ width: `${width}%`, minWidth: seconds > 0 ? 2 : 0 }}
                      />
                    ) : null}
                  </span>
                  <span className={styles.barReadout}>
                    <span className={styles.barValue}>{formatProbeSeconds(seconds)}</span>
                    <span className={styles.unit}>с</span>
                  </span>
                </li>
              );
            })}
          </ul>
          {scalable ? (
            <div className={styles.scale} aria-hidden="true">
              <span />
              <span className={styles.scaleTrack}>
                <span>0</span>
                <span>
                  {formatProbeSeconds(max)} с
                </span>
              </span>
              <span className={styles.scaleSpacer} />
            </div>
          ) : null}
        </div>
      ) : null}
      <div className={styles.factGrid}>
        <div className={styles.fact}>
          <h3 className={styles.factTitle}>Диапазон clock-age</h3>
          <p className={styles.factValue}>{formatClockAgeRangeSeconds(clockAgeRangeSeconds)}</p>
          <p className={styles.summary}>
            Не входит в шкалу latency. p95 не опубликован — в snapshot только диапазон clock-age.
          </p>
        </div>
        <div className={styles.fact}>
          <h3 className={styles.factTitle}>Стадия тестов</h3>
          <dl className={styles.pair}>
            <div>
              <dt>Base</dt>
              <dd>{recordedBaseTestsPassed}</dd>
            </div>
            <div>
              <dt>Combined</dt>
              <dd>{recordedCombinedTestsPassed}</dd>
            </div>
          </dl>
          <p className={styles.summary}>Стадия зафиксирована в snapshot, не live CI.</p>
        </div>
      </div>
    </section>
  );
}
