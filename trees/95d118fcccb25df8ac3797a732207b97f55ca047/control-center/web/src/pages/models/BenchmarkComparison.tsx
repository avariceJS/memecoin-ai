import { useMemo, useState } from "react";
import { QualificationBadge } from "../../components/status";
import { LatencyComparison } from "./LatencyComparison";
import {
  rowsForAxis,
  uniqueBatches,
  uniqueDevices,
  uniqueModels,
  uniqueShapes,
  type BenchmarkRow,
  type TimingMetric,
} from "./benchmarkFilters";
import styles from "./models.module.css";

type BenchmarkComparisonProps = {
  benchmarks: BenchmarkRow[];
  provenance: "REAL_EVIDENCE" | "DEMO";
  qualification: string;
};

function isMetric(value: string): value is TimingMetric {
  return value === "medianInferenceMs" || value === "medianForwardBackwardMs";
}

export function BenchmarkComparison({ benchmarks, provenance, qualification }: BenchmarkComparisonProps) {
  const models = useMemo(() => uniqueModels(benchmarks), [benchmarks]);
  const devices = useMemo(() => uniqueDevices(benchmarks), [benchmarks]);
  const shapes = useMemo(() => uniqueShapes(benchmarks), [benchmarks]);
  const batches = useMemo(() => uniqueBatches(benchmarks), [benchmarks]);
  const [model, setModel] = useState(models[0] ?? "");
  const [device, setDevice] = useState(devices[0] ?? "");
  const [shape, setShape] = useState(shapes[0]?.label ?? "");
  const [batch, setBatch] = useState(batches[0] ?? "");
  const [metric, setMetric] = useState<TimingMetric>("medianInferenceMs");

  const selection = { model, device, shape, batch };
  const deviceRows = rowsForAxis(benchmarks, "device", selection);
  const modelRows = rowsForAxis(benchmarks, "model", selection);
  const demo = provenance === "DEMO";
  const engineering =
    !demo && (qualification === "ENGINEERING_ONLY" || qualification === "ENGINEERING_CORPUS");
  const syntheticInput =
    !demo && benchmarks.length > 0 && benchmarks.every((row) => row.inputKind === "SYNTHETIC");

  return (
    <section
      className={styles.region}
      data-cc-region="benchmark-comparison"
      aria-labelledby="benchmark-comparison-title"
    >
      <div className={styles.regionHead}>
        <h2 id="benchmark-comparison-title" className={styles.sectionTitle}>
          Сопоставимые замеры
        </h2>
        <p className={styles.lede}>
          CPU и MPS — одна модель, форма, пакет и метрика. Архитектуры — одно устройство, форма и пакет.
          Несовпадение не получает шкалу.
        </p>
      </div>
      <div className={styles.plot}>
        <div className={styles.badgeRow}>
          {demo ? <QualificationBadge kind="DEMO" /> : null}
          {engineering ? <QualificationBadge kind="ENGINEERING" /> : null}
          {syntheticInput ? <QualificationBadge kind="SYNTHETIC INPUT" /> : null}
        </div>
        <div className={styles.filterRow}>
          <div className={styles.filterGroup}>
            <label className={styles.filterLabel} htmlFor="bench-model">
              Модель для CPU и MPS
            </label>
            <select
              id="bench-model"
              className={styles.filterSelect}
              value={model}
              onChange={(event) => setModel(event.target.value)}
            >
              {models.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </div>
          <div className={styles.filterGroup}>
            <label className={styles.filterLabel} htmlFor="bench-device">
              Устройство для архитектур
            </label>
            <select
              id="bench-device"
              className={styles.filterSelect}
              value={device}
              onChange={(event) => setDevice(event.target.value)}
            >
              {devices.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </div>
          <div className={styles.filterGroup}>
            <label className={styles.filterLabel} htmlFor="bench-shape">
              Форма
            </label>
            <select
              id="bench-shape"
              className={styles.filterSelect}
              value={shape}
              onChange={(event) => setShape(event.target.value)}
            >
              {shapes.map((item) => (
                <option key={item.label} value={item.label}>
                  {item.label}
                </option>
              ))}
            </select>
          </div>
          <div className={styles.filterGroup}>
            <label className={styles.filterLabel} htmlFor="bench-batch">
              Пакет
            </label>
            <select
              id="bench-batch"
              className={styles.filterSelect}
              value={batch}
              onChange={(event) => setBatch(event.target.value)}
            >
              {batches.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </div>
          <div className={styles.filterGroup}>
            <label className={styles.filterLabel} htmlFor="bench-metric">
              Метрика
            </label>
            <select
              id="bench-metric"
              className={styles.filterSelect}
              value={metric}
              onChange={(event) => {
                if (isMetric(event.target.value)) setMetric(event.target.value);
              }}
            >
              <option value="medianInferenceMs">медиана инференса</option>
              <option value="medianForwardBackwardMs">медиана прямого и обратного</option>
            </select>
          </div>
        </div>
        <LatencyComparison rows={deviceRows} mode="device" metric={metric} title="CPU и MPS" />
        <LatencyComparison rows={modelRows} mode="model" metric={metric} title="Архитектуры" />
      </div>
    </section>
  );
}
