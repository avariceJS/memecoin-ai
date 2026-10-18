import styles from "./UnavailableState.module.css";

type UnavailableStateProps = {
  reasonCode: string;
};

export function UnavailableState({ reasonCode }: UnavailableStateProps) {
  return (
    <div className={styles.root} role="status">
      <p className={styles.reason}>{reasonCode}</p>
    </div>
  );
}
