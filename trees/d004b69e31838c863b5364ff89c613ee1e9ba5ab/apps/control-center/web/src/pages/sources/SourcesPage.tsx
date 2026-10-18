import { useOutletContext } from "react-router";
import { QualificationBadge, UnavailableState } from "../../components/status";
import type { ShellOutletContext } from "../../shell/AppShell";
import { getSourceQualificationView } from "../overview/selectors";
import { LatencyProbeTable } from "./LatencyProbeTable";
import { PoolsCopyPanel } from "./PoolsCopyPanel";
import { SourceStampsTable } from "./SourceStampsTable";
import { SourcesStatusAside } from "./SourcesStatusAside";
import styles from "./sources.module.css";

function provenanceBadge(
  provenance: string | null,
): "REAL EVIDENCE" | "ENGINEERING" | "SYNTHETIC INPUT" {
  if (provenance === "REAL_EVIDENCE") return "REAL EVIDENCE";
  if (provenance === "DEMO") return "SYNTHETIC INPUT";
  return "ENGINEERING";
}

export function SourcesPage() {
  const { snapshotResponse } = useOutletContext<ShellOutletContext>();
  const snapshot = snapshotResponse.data;
  const qualification = getSourceQualificationView(snapshot);
  const pane = snapshot.sourceQualification;
  const stampCount = Object.keys(snapshot.sources).length;

  if (!qualification || pane.availability !== "AVAILABLE") {
    return (
      <UnavailableState
        reasonCode={
          pane.availability !== "AVAILABLE"
            ? pane.reasonCode
            : "SOURCE_QUALIFICATION_UNAVAILABLE"
        }
      />
    );
  }

  const provenanceLabel = provenanceBadge(pane.provenance);

  return (
    <>
      <header className={styles.pageHeader}>
        <div>
          <h2 className={styles.pageTitle}>Sources / Pools</h2>
          <p className={styles.policyNote}>
            {snapshot.sourcePolicy} · recorded evidence only · текущий operational status
            неизвестен.
          </p>
        </div>
        <div className={styles.badgeRow}>
          <QualificationBadge kind={provenanceLabel} />
          <QualificationBadge kind="LOCKED" />
        </div>
      </header>

      <div className={styles.kpiRow}>
        <article className={styles.kpi}>
          <p className={styles.kpiLabel}>Source stamps</p>
          <p className={styles.kpiValue}>{stampCount}</p>
        </article>
        <article className={styles.kpi}>
          <p className={styles.kpiLabel}>Probe acceptance (recorded)</p>
          <p className={styles.kpiValue}>
            {qualification.accepted}/{qualification.requests}
          </p>
          <p className={styles.kpiSub}>
            {qualification.accepted}/{qualification.requests} не означает текущий RPC health
          </p>
        </article>
        <article className={styles.kpi}>
          <p className={styles.kpiLabel}>Recorded tests</p>
          <p className={styles.kpiValue}>
            {qualification.recordedBaseTestsPassed}/{qualification.recordedCombinedTestsPassed}
          </p>
          <p className={styles.kpiSub}>зафиксированная стадия в snapshot</p>
        </article>
        <article className={styles.kpi}>
          <p className={styles.kpiLabel}>Pool IDs</p>
          <p className={styles.kpiValue}>{qualification.pools.length}</p>
        </article>
      </div>

      <div className={styles.mainGrid}>
        <div className={styles.leftStack}>
          <SourceStampsTable snapshot={snapshot} />
          <LatencyProbeTable qualification={qualification} />
        </div>
        <SourcesStatusAside qualification={qualification} provenanceLabel={provenanceLabel} />
      </div>

      <div className={styles.bottomGrid}>
        <PoolsCopyPanel qualification={qualification} />
      </div>
    </>
  );
}
