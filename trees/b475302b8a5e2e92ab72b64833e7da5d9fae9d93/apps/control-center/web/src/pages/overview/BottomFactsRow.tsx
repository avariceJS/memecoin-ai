import { useCallback, useState } from "react";
import { Panel } from "../../components";
import styles from "./overview.module.css";

type BottomFactsRowProps = {
  pools: string[] | null;
};

function visiblePoolId(poolId: string): string {
  if (poolId.length <= 16) return poolId;
  return `${poolId.slice(0, 8)}…${poolId.slice(-4)}`;
}

async function copyText(text: string): Promise<void> {
  if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  throw new Error("clipboard_unavailable");
}

export function BottomFactsRow({ pools }: BottomFactsRowProps) {
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
    <Panel title="Pool IDs" density="compact">
      {pools && pools.length > 0 ? (
        <ul className={styles.poolList}>
          {pools.map((poolId) => (
            <li key={poolId} className={styles.poolRow}>
              <p className={styles.poolId} title={poolId}>
                {visiblePoolId(poolId)}
              </p>
              <button
                type="button"
                className={styles.copyBtn}
                aria-label={`Копировать pool id ${poolId}`}
                onClick={() => void onCopy(poolId)}
              >
                {copiedId === poolId ? "Скопировано" : failedId === poolId ? "Не скопировано" : "Копировать"}
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className={styles.note}>Pool id в snapshot не опубликованы.</p>
      )}
      <p className={styles.note}>Записанные идентификаторы. Это не текущее подключение.</p>
    </Panel>
  );
}
