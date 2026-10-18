import { useSnapshot } from "../../api/hooks";
import { UnavailableState } from "../../components/status";
import styles from "./system.module.css";

export function AdapterReachabilityCard() {
  const snapshotQuery = useSnapshot();

  return (
    <section className={styles.panel} aria-labelledby="adapter-reach-heading">
      <h3 id="adapter-reach-heading" className={styles.panelTitle}>
        Достижимость UI adapter
      </h3>
      {snapshotQuery.isSuccess && snapshotQuery.data ? (
        <>
          <p className={styles.adapterOk}>UI adapter ответил</p>
          <p className={styles.caption}>
            snapshot{" "}
            <span className={styles.captionStrong}>{snapshotQuery.data.snapshotId}</span>
          </p>
          <p className={styles.caption}>
            servedAt{" "}
            <span className={styles.captionStrong}>{snapshotQuery.data.servedAt}</span>
            {" — "}
            время ответа API UI adapter, не рынок и не core heartbeat.
          </p>
        </>
      ) : snapshotQuery.isError ? (
        <>
          <p className={styles.adapterFail}>UI adapter не подтверждён</p>
          <p className={styles.caption}>
            Запрос snapshot завершился ошибкой — нельзя считать adapter healthy.
          </p>
          <div className={styles.reasonBlock}>
            <UnavailableState reasonCode="SNAPSHOT_QUERY_FAILED" />
          </div>
        </>
      ) : (
        <p className={styles.caption}>Ожидание ответа snapshot…</p>
      )}
    </section>
  );
}
