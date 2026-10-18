import { useOutletContext } from "react-router";
import type { Capability } from "../../api";
import { QualificationBadge } from "../../components/status";
import type { ShellOutletContext } from "../../shell/AppShell";
import styles from "./replay.module.css";

function replayBadge(cap: Capability | undefined): "UNAVAILABLE" | "LOCKED" {
  if (!cap || cap.availability === "UNAVAILABLE") return "UNAVAILABLE";
  return "LOCKED";
}

function replayReason(cap: Capability | undefined): string {
  if (!cap) return "CAPABILITY_NOT_PUBLISHED";
  if (cap.availability === "UNAVAILABLE") return cap.reasonCode;
  return cap.mode;
}

export function ReplayPage() {
  const { capabilities } = useOutletContext<ShellOutletContext>();
  const replay = capabilities.find((capability) => capability.id === "replay");
  const reason = replayReason(replay);
  const badge = replayBadge(replay);

  return (
    <section
      className={styles.closedCard}
      data-tone={badge === "LOCKED" ? "locked" : "unavailable"}
      aria-labelledby="replay-closed-title"
    >
      <div className={styles.closedHead}>
        <h2 id="replay-closed-title" className={styles.closedTitle}>
          Replay
        </h2>
        <QualificationBadge kind={badge} />
      </div>
      <p className={styles.closedSummary}>
        Повтор событий и импорт логов закрыты. Отдельный export (CC-P001) в этот экран не входит.
      </p>
      <div
        className={styles.codeScroll}
        data-cc-scroll="replay-reason"
        tabIndex={0}
        aria-label="Код контракта replay"
      >
        {reason}
      </div>
    </section>
  );
}
