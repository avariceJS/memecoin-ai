import type { Snapshot } from "../../api";
import { ModelsStatusAside } from "./ModelsStatusAside";
import { formatCount, runsLabel } from "./format";
import styles from "./models.module.css";
import { architectureBars } from "./viewModel";

type ModelsData = Extract<Snapshot["models"], { availability: "AVAILABLE" }>["data"];

type ArchitectureSummaryProps = {
  data: ModelsData;
};

export function ArchitectureSummary({ data }: ArchitectureSummaryProps) {
  const bars = architectureBars(data.runs);
  const known = bars.map((bar) => bar.parameters).filter((value): value is number => value !== null);
  const max = known.length > 0 ? Math.max(...known) : 0;
  const realRuns = data.runs.filter((run) => run.datasetKind === "REAL").length;
  const syntheticRuns = data.runs.length - realRuns;

  return (
    <section className={styles.region} data-cc-region="model-summary" aria-labelledby="model-summary-title">
      <div className={styles.regionHead}>
        <h2 id="model-summary-title" className={styles.sectionTitle}>
          Сводка архитектур
        </h2>
        <p className={styles.lede}>
          Число параметров по записанным прогонам. Длина полосы — params, не скорость и не качество рынка.
        </p>
      </div>
      <p className={styles.facts}>
        <span>
          <strong>{data.runs.length}</strong> прогонов
        </span>
        <span>
          <strong>{data.benchmarks.length}</strong> замеров
        </span>
        <span>{syntheticRuns} прогонов SYNTHETIC</span>
        <span>{realRuns} прогонов на REAL dataset</span>
      </p>
      <ul className={styles.archList} aria-label="Параметры архитектур">
        {bars.map((bar) => {
          const width = bar.parameters === null || max <= 0 ? 0 : (bar.parameters / max) * 100;
          return (
            <li key={bar.modelKind} className={styles.archRow}>
              <div className={styles.archMeta}>
                <span className={styles.barName} title={bar.modelKind}>
                  {bar.modelKind}
                </span>
                <span className={styles.archCounts}>
                  {runsLabel(bar.runs)} · {bar.syntheticRuns} SYNTHETIC · {bar.realRuns} REAL dataset
                </span>
              </div>
              <div className={styles.archTrack} aria-hidden="true">
                <span className={styles.fill} data-tone={bar.modelKind} style={{ width: `${width}%` }} />
              </div>
              <span className={styles.barValue}>
                {bar.parameters === null ? "не совпали" : formatCount(bar.parameters)}
              </span>
            </li>
          );
        })}
      </ul>
      <p className={styles.chartCaption}>Порядок по имени. В этой проекции нет весов, checkpoints и торгового runtime.</p>
      <ModelsStatusAside data={data} />
    </section>
  );
}
