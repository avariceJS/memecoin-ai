import { useOutletContext } from "react-router";
import { QualificationBadge, UnavailableState } from "../../components/status";
import type { ShellOutletContext } from "../../shell/AppShell";
import { BenchmarkTimingsTable } from "./BenchmarkTimingsTable";
import { EngineeringRunsTable } from "./EngineeringRunsTable";
import { ModelsStatusAside } from "./ModelsStatusAside";
import styles from "./models.module.css";

export function ModelsPage() {
  const { snapshotResponse } = useOutletContext<ShellOutletContext>();
  const snapshot = snapshotResponse.data;
  const pane = snapshot.models;

  if (pane.availability !== "AVAILABLE") {
    return <UnavailableState reasonCode={pane.reasonCode} />;
  }

  const { data } = pane;
  const realRuns = data.runs.filter((r) => r.datasetKind === "REAL").length;
  const syntheticRuns = data.runs.length - realRuns;

  return (
    <>
      <header className={styles.pageHeader}>
        <div>
          <h2 className={styles.pageTitle}>Models / Engineering evidence</h2>
          <p className={styles.policyNote}>
            {snapshot.sourcePolicy} · recorded D44 smoke runs и synthetic-input benchmarks ·
            без weights/checkpoints и без торгового runtime.
          </p>
        </div>
        <div className={styles.badgeRow}>
          <QualificationBadge kind="ENGINEERING" />
          <QualificationBadge kind="SYNTHETIC INPUT" />
        </div>
      </header>

      <div className={styles.kpiRow}>
        <article className={styles.kpi}>
          <p className={styles.kpiLabel}>Engineering runs</p>
          <p className={styles.kpiValue}>{data.runs.length}</p>
          <p className={styles.kpiSub}>ENGINEERING_SMOKE scope</p>
        </article>
        <article className={styles.kpi}>
          <p className={styles.kpiLabel}>Benchmark cases</p>
          <p className={styles.kpiValue}>{data.benchmarks.length}</p>
          <p className={styles.kpiSub}>SYNTHETIC input timings</p>
        </article>
        <article className={styles.kpi}>
          <p className={styles.kpiLabel}>REAL dataset runs</p>
          <p className={styles.kpiValue}>{realRuns}</p>
          <p className={styles.kpiSub}>отдельно от SYNTHETIC smoke</p>
        </article>
        <article className={styles.kpi}>
          <p className={styles.kpiLabel}>SYNTHETIC dataset runs</p>
          <p className={styles.kpiValue}>{syntheticRuns}</p>
        </article>
      </div>

      <div className={styles.mainGrid}>
        <div className={styles.stack}>
          <EngineeringRunsTable runs={data.runs} />
          <BenchmarkTimingsTable benchmarks={data.benchmarks} />
        </div>
        <ModelsStatusAside data={data} />
      </div>
    </>
  );
}
