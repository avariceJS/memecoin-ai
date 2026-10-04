import { useCallback, useEffect, useId, useRef, useState } from "react";
import { QualificationBadge, type ProvenanceBadgeKind } from "../../components/status";
import type { SourceQualificationView } from "../overview/selectors";
import styles from "./sources.module.css";
import { shortenPoolId } from "./viewModel";

type PoolsCopyPanelProps = {
  qualification: SourceQualificationView;
  provenance: ProvenanceBadgeKind;
};

async function copyText(text: string): Promise<void> {
  if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  throw new Error("clipboard_unavailable");
}

export function PoolsCopyPanel({ qualification, provenance }: PoolsCopyPanelProps) {
  const titleId = useId();
  const { pools } = qualification;
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [failedId, setFailedId] = useState<string | null>(null);
  const timers = useRef<number[]>([]);

  useEffect(() => {
    const active = timers.current;
    return () => {
      for (const id of active) window.clearTimeout(id);
    };
  }, []);

  const onCopy = useCallback(async (poolId: string) => {
    try {
      await copyText(poolId);
      setFailedId((current) => (current === poolId ? null : current));
      setCopiedId(poolId);
      const timer = window.setTimeout(() => {
        setCopiedId((current) => (current === poolId ? null : current));
      }, 2000);
      timers.current.push(timer);
    } catch {
      setCopiedId((current) => (current === poolId ? null : current));
      setFailedId(poolId);
    }
  }, []);

  return (
    <section className={styles.panel} data-cc-region="pool-ids" aria-labelledby={titleId}>
      <div className={styles.panelHead}>
        <h2 id={titleId} className={styles.panelTitle}>
          Публичные pool id
        </h2>
        <QualificationBadge kind={provenance} />
      </div>
      <p className={styles.summary}>
        Короткая форма в карточке. Полный id копируется локально, без explorer и без сети.
      </p>
      {pools.length === 0 ? <p className={styles.summary}>Публичные pool id не опубликованы.</p> : null}
      <ul className={styles.poolList}>
        {pools.map((poolId) => {
          const shortId = shortenPoolId(poolId);
          const copied = copiedId === poolId;
          const failed = failedId === poolId;
          return (
            <li key={poolId} className={styles.poolCard}>
              <p className={styles.shortId}>{shortId}</p>
              <div
                className={styles.idScroll}
                data-cc-scroll="pool-id"
                tabIndex={0}
                aria-label={`Полный pool id ${shortId}`}
              >
                {poolId}
              </div>
              <button
                type="button"
                className={styles.textButton}
                aria-label={`Копировать pool id ${shortId}`}
                onClick={() => void onCopy(poolId)}
              >
                {copied ? "Скопировано" : failed ? "Не скопировано" : "Копировать"}
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
