import { ControlCenterApiError } from "../../api/errors";
import styles from "./ErrorState.module.css";

type ErrorStateProps = {
  error: Error;
  onRetry: () => void;
};

function safeCode(error: Error): string {
  if (error instanceof ControlCenterApiError) {
    return error.code;
  }
  return "FETCH_FAILED";
}

export function ErrorState({ error, onRetry }: ErrorStateProps) {
  const code = safeCode(error);
  return (
    <div className={styles.root} role="alert">
      <p className={styles.code}>{code}</p>
      <button type="button" className={styles.button} onClick={onRetry}>
        Повторить
      </button>
    </div>
  );
}
