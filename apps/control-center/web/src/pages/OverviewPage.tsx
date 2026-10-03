import { useOutletContext } from "react-router";
import type { ShellOutletContext } from "../shell/AppShell";
import {
  calendarWindowCount,
  engineeringFits,
  recordedFits,
  recordedVerdict,
  runCount,
} from "./snapshotFacts";
import styles from "./shared.module.css";

export function OverviewPage() {
  const { snapshotResponse, capabilities } = useOutletContext<ShellOutletContext>();
  const snapshot = snapshotResponse.data;

  const windows = calendarWindowCount(snapshot);
  const fits = recordedFits(snapshot);
  const engFits = engineeringFits(snapshot);
  const runs = runCount(snapshot);
  const verdict = recordedVerdict(snapshot);
  const systemReason =
    snapshot.system.availability !== "AVAILABLE" ? snapshot.system.reasonCode : null;

  const sources = Object.entries(snapshot.sources).sort(([a], [b]) => a.localeCompare(b));

  const available = capabilities.filter((c) => c.availability === "AVAILABLE");
  const unavailable = capabilities.filter((c) => c.availability === "UNAVAILABLE");

  return (
    <>
      <div className={styles.kpiRow}>
        <article className={styles.kpi}>
          <p className={styles.kpiLabel}>Окна календаря</p>
          <p className={styles.kpiValue}>{windows ?? "—"}</p>
        </article>
        <article className={styles.kpi}>
          <p className={styles.kpiLabel}>Recorded fits</p>
          <p className={styles.kpiValue}>
            {fits ? `${fits.recorded}/${fits.budget}` : "—"}
          </p>
          <p className={styles.kpiSub}>только recorded budget</p>
        </article>
        <article className={styles.kpi}>
          <p className={styles.kpiLabel}>Engineering fits</p>
          <p className={styles.kpiValue}>{engFits ?? "—"}</p>
          <p className={styles.kpiSub}>ENGINEERING, не admission</p>
        </article>
        <article className={styles.kpi}>
          <p className={styles.kpiLabel}>Инженерные runs</p>
          <p className={styles.kpiValue}>{runs ?? "—"}</p>
        </article>
      </div>

      <div className={styles.overviewGrid}>
        <section className={styles.panel} aria-labelledby="sources-heading">
          <h2 id="sources-heading" className={styles.panelTitle}>
            Источники проекции
          </h2>
          <div className={styles.sourceGrid}>
            {sources.map(([id, stamp]) => (
              <article key={id} className={styles.sourceCard}>
                <p className={styles.sourceId}>{id}</p>
                <p className={styles.sourceMeta}>
                  evidence {stamp.evidenceDate}
                  <br />
                  {stamp.integrity}
                  <br />
                  {stamp.recordedAtNs ?? "время не опубликовано"}
                </p>
              </article>
            ))}
          </div>
        </section>

        <aside className={styles.verdictPanel} aria-labelledby="verdict-heading">
          <h2 id="verdict-heading" className={styles.verdictTitle}>
            Recorded verdict
          </h2>
          <p className={styles.verdictCode}>{verdict ?? "UNAVAILABLE"}</p>
          <p className={styles.verdictNote}>
            Текущее здоровье сервиса неизвестно: pane system{" "}
            {systemReason ?? "UNAVAILABLE"} — UI adapter не подтверждает core online.
          </p>
        </aside>
      </div>

      <section className={styles.capList} aria-labelledby="caps-heading">
        <h2 id="caps-heading" className={styles.panelTitle}>
          Capabilities
        </h2>
        <ul className={styles.capRows}>
          {available.map((cap) => (
            <li key={cap.id} className={styles.capRow}>
              <span className={styles.capId}>{cap.id}</span>
              <span className={`${styles.capStatus} ${styles.capAvailable}`}>AVAILABLE</span>
            </li>
          ))}
          {unavailable.map((cap) => (
            <li key={cap.id} className={styles.capRow}>
              <span className={styles.capId}>{cap.id}</span>
              <span className={`${styles.capStatus} ${styles.capUnavailable}`}>
                UNAVAILABLE · {cap.reasonCode}
              </span>
            </li>
          ))}
        </ul>
      </section>
    </>
  );
}
