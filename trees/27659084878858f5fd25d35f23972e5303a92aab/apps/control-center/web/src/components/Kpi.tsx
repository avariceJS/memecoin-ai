import styles from "./Kpi.module.css";

type KpiProps = {
  label: string;
  value: string;
  context?: string;
  compact?: boolean;
};

export function Kpi({ label, value, context, compact = false }: KpiProps) {
  return (
    <div className={compact ? styles.compact : styles.kpi}>
      <p className={styles.label}>{label}</p>
      <p className={styles.value}>{value}</p>
      {context ? <p className={styles.context}>{context}</p> : null}
    </div>
  );
}
