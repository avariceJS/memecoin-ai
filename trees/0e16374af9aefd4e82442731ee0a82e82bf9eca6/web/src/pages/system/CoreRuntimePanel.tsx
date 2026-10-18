import type { Snapshot } from "../../api";
import { QualificationBadge, UnavailableState } from "../../components/status";
import styles from "./system.module.css";

type CoreRuntimePanelProps = {
  systemPane: Snapshot["system"];
};

export function CoreRuntimePanel({ systemPane }: CoreRuntimePanelProps) {
  const reason =
    systemPane.availability !== "AVAILABLE"
      ? systemPane.reasonCode
      : "NO_CURRENT_SERVICE_OR_HARDWARE_FEED";

  return (
    <section className={styles.panel} aria-labelledby="core-runtime-heading">
      <h3 id="core-runtime-heading" className={styles.panelTitle}>
        Core runtime (pane system)
      </h3>
      <QualificationBadge kind="UNAVAILABLE" />
      <p className={styles.corePaneNote}>
        Pane system UNAVAILABLE — {reason}. Adapter не подтверждает core online, CPU/RAM/MPS и
        process scan отсутствуют.
      </p>
      <div className={styles.reasonBlock}>
        <UnavailableState reasonCode={reason} />
      </div>
    </section>
  );
}
