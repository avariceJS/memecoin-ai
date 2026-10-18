import { useOutletContext } from "react-router";
import type { ShellOutletContext } from "../../shell/AppShell";
import { AdapterReachabilityCard } from "./AdapterReachabilityCard";
import { BuildStrip } from "./BuildStrip";
import { CapabilityMatrix } from "./CapabilityMatrix";
import { CoreRuntimePanel } from "./CoreRuntimePanel";
import { DesignRoleGrid } from "./DesignRoleGrid";
import { RecordedCoreTestsPanel } from "./RecordedCoreTestsPanel";
import styles from "./system.module.css";

export function SystemPage() {
  const { snapshotResponse, capabilities } = useOutletContext<ShellOutletContext>();
  const snapshot = snapshotResponse.data;

  return (
    <div className={styles.page}>
      <header className={styles.pageHeader}>
        <h2 className={styles.pageTitle}>Границы возможностей</h2>
        <p className={styles.policyNote}>
          Ответ UI-adapter и неизвестное состояние core показаны отдельно. Ниже — контракт
          snapshot, без health, процессов и действий.
        </p>
      </header>
      <div className={styles.statusBand}>
        <AdapterReachabilityCard />
        <CoreRuntimePanel systemPane={snapshot.system} />
        <RecordedCoreTestsPanel qualificationPane={snapshot.sourceQualification} />
      </div>
      <BuildStrip snapshot={snapshot} />
      <CapabilityMatrix capabilities={capabilities} />
      <DesignRoleGrid />
    </div>
  );
}
