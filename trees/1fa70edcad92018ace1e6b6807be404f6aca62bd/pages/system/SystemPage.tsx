import { useOutletContext } from "react-router";
import type { ShellOutletContext } from "../../shell/AppShell";
import { AdapterReachabilityCard } from "./AdapterReachabilityCard";
import { CoreRuntimePanel } from "./CoreRuntimePanel";
import { DesignRoleGrid } from "./DesignRoleGrid";
import { FutureSurfacesGrid } from "./FutureSurfacesGrid";
import { RecordedCoreTestsPanel } from "./RecordedCoreTestsPanel";
import styles from "./system.module.css";

export function SystemPage() {
  const { snapshotResponse, capabilities } = useOutletContext<ShellOutletContext>();
  const snapshot = snapshotResponse.data;

  return (
    <>
      <header className={styles.pageHeader}>
        <h2 className={styles.pageTitle}>Система — границы и adapter</h2>
        <p className={styles.policyNote}>
          Reachability UI adapter отделена от recorded core tests и pane system. Графиков
          CPU/RAM/MPS и сканирования процессов нет.
        </p>
      </header>

      <div className={styles.mainGrid}>
        <div className={styles.leftStack}>
          <DesignRoleGrid />
          <RecordedCoreTestsPanel qualificationPane={snapshot.sourceQualification} />
        </div>
        <aside className={styles.statusStack}>
          <AdapterReachabilityCard />
          <CoreRuntimePanel systemPane={snapshot.system} />
          <FutureSurfacesGrid capabilities={capabilities} />
        </aside>
      </div>
    </>
  );
}
