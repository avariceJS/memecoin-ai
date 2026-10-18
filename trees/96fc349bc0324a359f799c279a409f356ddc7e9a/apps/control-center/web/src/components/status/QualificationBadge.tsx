import styles from "./QualificationBadge.module.css";

export type QualificationBadgeKind =
  | "REAL EVIDENCE"
  | "ENGINEERING"
  | "SYNTHETIC INPUT"
  | "PROTECTED"
  | "LOCKED"
  | "UNAVAILABLE";

const kindClass: Record<QualificationBadgeKind, string> = {
  "REAL EVIDENCE": styles.realEvidence,
  ENGINEERING: styles.engineering,
  "SYNTHETIC INPUT": styles.synthetic,
  PROTECTED: styles.protected,
  LOCKED: styles.locked,
  UNAVAILABLE: styles.unavailable,
};

type QualificationBadgeProps = {
  kind: QualificationBadgeKind;
};

export function QualificationBadge({ kind }: QualificationBadgeProps) {
  return (
    <span className={`${styles.badge} ${kindClass[kind]}`} aria-label={kind}>
      {kind}
    </span>
  );
}
