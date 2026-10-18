import { useCallback, useState } from "react";
import type { SourceQualificationView } from "../overview/selectors";
import styles from "./sources.module.css";

type PoolsCopyPanelProps = {
  qualification: SourceQualificationView;
};

async function copyText(text: string): Promise<void> {
  if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  throw new Error("clipboard_unavailable");
}

export function PoolsCopyPanel({ qualification }: PoolsCopyPanelProps) {
  const { pools } = qualification;
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [failedId, setFailedId] = useState<string | null>(null);

  const onCopy = useCallback(async (poolId: string) => {
    try {
      await copyText(poolId);
      setFailedId((current) => (current === poolId ? null : current));
      setCopiedId(poolId);
      window.setTimeout(() => setCopiedId((current) => (current === poolId ? null : current)), 2000);
    } catch {
      setCopiedId((current) => (current === poolId ? null : current));
      setFailedId(poolId);
    }
  }, []);

  return (
    <section className={styles.panel} aria-labelledby="pools-copy-heading">
      <h2 id="pools-copy-heading" className={styles.panelTitle}>
        Pool IDs (recorded)
      </h2>
      <p className={styles.panelHint}>
        Копирование в буфер без explorer и без сетевых запросов.
      </p>
      <ul className={styles.poolList}>
        {pools.map((poolId) => (
          <li key={poolId} className={styles.poolRow}>
            <p className={styles.poolId} title={poolId}>
              {poolId}
            </p>
            <button
              type="button"
              className={styles.copyBtn}
              aria-label={`Копировать pool id ${poolId}`}
              onClick={() => void onCopy(poolId)}
            >
              {copiedId === poolId
                ? "Скопировано"
                : failedId === poolId
                  ? "Не скопировано"
                  : "Копировать pool id"}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
