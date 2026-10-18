import { useOutletContext } from "react-router";
import { QualificationBadge, UnavailableState } from "../../components/status";
import type { Capability } from "../../api";
import type { ShellOutletContext } from "../../shell/AppShell";
import styles from "./replay.module.css";

function replayBadge(cap: Capability | undefined): "UNAVAILABLE" | "LOCKED" {
  if (!cap) return "UNAVAILABLE";
  if (cap.availability === "UNAVAILABLE") return "UNAVAILABLE";
  return "LOCKED";
}

function replayReason(cap: Capability | undefined): string {
  if (!cap) return "CAPABILITY_NOT_PUBLISHED";
  if (cap.availability === "UNAVAILABLE") return cap.reasonCode;
  return "READ_ONLY_RECORDED_ONLY";
}

export function ReplayPage() {
  const { capabilities } = useOutletContext<ShellOutletContext>();
  const replay = capabilities.find((c) => c.id === "replay");
  const reason = replayReason(replay);
  const badge = replayBadge(replay);

  return (
    <>
      <header className={styles.pageHeader}>
        <div>
          <h2 className={styles.pageTitle}>Replay</h2>
          <p className={styles.policyNote}>
            Raw replay и импорт event logs в UI отсутствуют до отдельного core-approved
            operational export (proposal CC-P001). Snapshot остаётся read-only engineering
            projection без simulator control plane.
          </p>
        </div>
        <div className={styles.badgeRow}>
          <QualificationBadge kind={badge} />
        </div>
      </header>

      <div className={styles.unavailableBlock}>
        <UnavailableState reasonCode={reason} />
      </div>

      <p className={styles.boundaryNote}>
        Нет загрузки файлов, нет запуска replay job и нет write-операций — только capability
        contract из уже загруженного snapshot через shell outlet.
      </p>
    </>
  );
}
