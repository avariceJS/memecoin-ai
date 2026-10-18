import styles from "./LoadingState.module.css";

export function LoadingState() {
  return (
    <div className={styles.root} role="status" aria-live="polite">
      <p className={styles.label}>Загрузка проекции</p>
    </div>
  );
}
