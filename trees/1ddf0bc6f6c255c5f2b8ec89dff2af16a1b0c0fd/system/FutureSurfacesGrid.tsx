import type { Capability } from "../../api";
import { QualificationBadge } from "../../components/status";
import { FUTURE_SURFACE_IDS } from "./futureSurfaces";
import styles from "./system.module.css";

type FutureSurfacesGridProps = {
  capabilities: Capability[];
};

function surfaceBadge(cap: Capability | undefined): "LOCKED" | "UNAVAILABLE" {
  if (!cap) return "UNAVAILABLE";
  if (cap.availability === "UNAVAILABLE") return "UNAVAILABLE";
  return "LOCKED";
}

function surfaceReason(cap: Capability | undefined): string {
  if (!cap) return "CAPABILITY_NOT_PUBLISHED";
  if (cap.availability === "UNAVAILABLE") return cap.reasonCode;
  return "READ_ONLY_RECORDED_ONLY";
}

export function FutureSurfacesGrid({ capabilities }: FutureSurfacesGridProps) {
  const byId = new Map(capabilities.map((c) => [c.id, c]));

  return (
    <section className={styles.panel} aria-labelledby="future-surfaces-heading">
      <h3 id="future-surfaces-heading" className={styles.panelTitle}>
        Будущие surfaces
      </h3>
      <p className={styles.caption}>
        Без кнопок и имитации успеха — только capability contract из snapshot.
      </p>
      <ul className={styles.surfaceGrid}>
        {FUTURE_SURFACE_IDS.map((id) => {
          const cap = byId.get(id);
          const badge = surfaceBadge(cap);
          const reason = surfaceReason(cap);
          return (
            <li key={id} className={styles.surfaceRow}>
              <div className={styles.surfaceHead}>
                <p className={styles.surfaceId}>{id}</p>
                <QualificationBadge kind={badge} />
              </div>
              <p className={styles.surfaceReason}>{reason}</p>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
