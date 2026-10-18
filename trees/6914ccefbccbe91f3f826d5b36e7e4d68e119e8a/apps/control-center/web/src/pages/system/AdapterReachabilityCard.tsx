import { useSnapshot } from "../../api/hooks";
import { CodeLine } from "./CodeLine";
import styles from "./system.module.css";

export function AdapterReachabilityCard() {
  const snapshotQuery = useSnapshot();

  return (
    <section
      className={styles.panel}
      aria-labelledby="adapter-reach-heading"
      data-cc-region="adapter-response"
    >
      <h3 id="adapter-reach-heading" className={styles.panelTitle}>
        Ответ UI-adapter
      </h3>
      {snapshotQuery.isSuccess && snapshotQuery.data ? (
        <>
          <p className={styles.adapterOk}>UI adapter ответил</p>
          <p className={styles.caption}>
            servedAt{" "}
            <span className={styles.captionStrong}>{snapshotQuery.data.servedAt}</span>
            {" — время ответа API, не heartbeat core"}
          </p>
          <CodeLine
            scrollId="adapter-snapshot-id"
            label="Идентификатор snapshot"
            value={snapshotQuery.data.snapshotId}
          />
        </>
      ) : snapshotQuery.isError ? (
        <>
          <p className={styles.adapterFail}>UI adapter не подтверждён</p>
          <p className={styles.caption}>
            Запрос snapshot завершился ошибкой. Это не состояние core.
          </p>
          <CodeLine
            scrollId="adapter-error"
            label="Код ответа adapter"
            value="SNAPSHOT_QUERY_FAILED"
          />
        </>
      ) : (
        <p className={styles.caption}>Ожидание ответа snapshot…</p>
      )}
    </section>
  );
}
