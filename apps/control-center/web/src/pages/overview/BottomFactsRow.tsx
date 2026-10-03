import { useCallback, useState } from "react";
import type { SourceQualificationView } from "./selectors";
import styles from "./overview.module.css";

type BottomFactsRowProps = {
  qualification: SourceQualificationView;
};

async function copyText(text: string): Promise<void> {
  if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  throw new Error("clipboard_unavailable");
}

export function BottomFactsRow({ qualification }: BottomFactsRowProps) {
  const { pools, requests, accepted, recordedBaseTestsPassed, recordedCombinedTestsPassed } =
    qualification;
  const [copiedId, setCopiedId] = useState<string | null>(null);

  const onCopy = useCallback(async (poolId: string) => {
    await copyText(poolId);
    setCopiedId(poolId);
    window.setTimeout(() => setCopiedId((current) => (current === poolId ? null : current)), 2000);
  }, []);

  return (
    <div className={styles.bottomGrid}>
      <section className={styles.factPanel} aria-labelledby="pools-heading">
        <h3 id="pools-heading" className={styles.factTitle}>
          Pool IDs (recorded)
        </h3>
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
                {copiedId === poolId ? "Скопировано" : "Копировать"}
              </button>
            </li>
          ))}
        </ul>
      </section>

      <section className={styles.factPanel} aria-labelledby="probe-heading">
        <h3 id="probe-heading" className={styles.factTitle}>
          Probe acceptance
        </h3>
        <p className={styles.metaLine}>
          <span className={styles.metaStrong}>
            {accepted}/{requests}
          </span>{" "}
          принято в recorded qualification
        </p>
        <p className={styles.probeNote}>Не трактовать как текущее здоровье сети или RPC.</p>
      </section>

      <section className={styles.factPanel} aria-labelledby="tests-heading">
        <h3 id="tests-heading" className={styles.factTitle}>
          Recorded tests stage
        </h3>
        <p className={styles.metaLine}>
          Base{" "}
          <span className={styles.metaStrong}>{recordedBaseTestsPassed}</span> / combined{" "}
          <span className={styles.metaStrong}>{recordedCombinedTestsPassed}</span>
        </p>
        <p className={styles.p95Note}>Стадия зафиксирована в snapshot, не live CI.</p>
      </section>

      <section className={styles.factPanel} aria-labelledby="evidence-meta-heading">
        <h3 id="evidence-meta-heading" className={styles.factTitle}>
          Evidence scope
        </h3>
        <p className={styles.metaLine}>
          Latency: <span className={styles.metaStrong}>6</span> probe, единицы — секунды.
        </p>
        <p className={styles.metaLine}>
          Clock-age: <span className={styles.metaStrong}>2</span> границы диапазона в snapshot.
        </p>
      </section>
    </div>
  );
}
