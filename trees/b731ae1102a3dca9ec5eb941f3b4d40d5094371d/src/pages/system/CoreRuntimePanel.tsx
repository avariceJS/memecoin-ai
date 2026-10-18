import type { Snapshot } from "../../api";
import { QualificationBadge } from "../../components/status";
import { CodeLine } from "./CodeLine";
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
    <section className={styles.panel} aria-labelledby="core-runtime-heading" data-cc-region="core-state">
      <div className={styles.panelHead}>
        <h3 id="core-runtime-heading" className={styles.panelTitle}>
          Состояние core
        </h3>
        <QualificationBadge kind="UNAVAILABLE" />
      </div>
      <p className={styles.caption}>
        Pane system не подтверждает запущенный core. Ответ adapter сюда не подставляется. Hardware
        feed и обзор процессов отсутствуют.
      </p>
      <CodeLine scrollId="core-reason" label="Код pane system" value={reason} />
    </section>
  );
}
