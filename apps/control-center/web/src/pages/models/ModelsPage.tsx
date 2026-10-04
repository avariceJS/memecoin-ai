import { useOutletContext } from "react-router";
import { UnavailableState } from "../../components/status";
import type { ShellOutletContext } from "../../shell/AppShell";
import { ArchitectureSummary } from "./ArchitectureSummary";
import { BenchmarkComparison } from "./BenchmarkComparison";
import { BenchmarkTimingsTable } from "./BenchmarkTimingsTable";
import { EngineeringRunsTable } from "./EngineeringRunsTable";
import styles from "./models.module.css";

export function ModelsPage() {
  const { snapshotResponse } = useOutletContext<ShellOutletContext>();
  const snapshot = snapshotResponse.data;
  const pane = snapshot.models;

  if (pane.availability !== "AVAILABLE") {
    return <UnavailableState reasonCode={pane.reasonCode} />;
  }

  return (
    <div className={styles.page}>
      <p className={styles.policy}>
        {snapshot.sourcePolicy}. Сводка архитектур и сопоставимые инженерные замеры. Параметры и задержка не
        качество рынка.
      </p>
      <div className={styles.stage}>
        <ArchitectureSummary data={pane.data} />
        <BenchmarkComparison
          benchmarks={pane.data.benchmarks}
          provenance={pane.provenance}
          qualification={pane.qualification}
        />
      </div>
      <div className={styles.tableStack}>
        <EngineeringRunsTable runs={pane.data.runs} />
        <BenchmarkTimingsTable benchmarks={pane.data.benchmarks} />
      </div>
    </div>
  );
}
