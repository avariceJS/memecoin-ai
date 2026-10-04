import styles from "./QualificationBadge.module.css";

export type QualificationBadgeKind =
  | "REAL EVIDENCE"
  | "ENGINEERING"
  | "SYNTHETIC INPUT"
  | "DEMO"
  | "PROTECTED"
  | "LOCKED"
  | "UNAVAILABLE";

export type ProvenanceBadgeKind = Extract<
  QualificationBadgeKind,
  "REAL EVIDENCE" | "ENGINEERING" | "DEMO"
>;

const kindClass: Record<QualificationBadgeKind, string> = {
  "REAL EVIDENCE": styles.realEvidence,
  ENGINEERING: styles.engineering,
  "SYNTHETIC INPUT": styles.synthetic,
  DEMO: styles.demo,
  PROTECTED: styles.protected,
  LOCKED: styles.locked,
  UNAVAILABLE: styles.unavailable,
};

const badgeLabel: Record<QualificationBadgeKind, string> = {
  "REAL EVIDENCE": "REAL EVIDENCE",
  ENGINEERING: "ENGINEERING",
  "SYNTHETIC INPUT": "SYNTHETIC INPUT",
  DEMO: "DEMO — вымышленные данные",
  PROTECTED: "PROTECTED",
  LOCKED: "LOCKED",
  UNAVAILABLE: "UNAVAILABLE",
};

type QualificationBadgeProps = {
  kind: QualificationBadgeKind;
};

export function provenanceBadgeKind(provenance: string | null): ProvenanceBadgeKind {
  if (provenance === "REAL_EVIDENCE") return "REAL EVIDENCE";
  if (provenance === "DEMO") return "DEMO";
  return "ENGINEERING";
}

export function QualificationBadge({ kind }: QualificationBadgeProps) {
  const label = badgeLabel[kind];
  return (
    <span className={`${styles.badge} ${kindClass[kind]}`} aria-label={label}>
      {label}
    </span>
  );
}
