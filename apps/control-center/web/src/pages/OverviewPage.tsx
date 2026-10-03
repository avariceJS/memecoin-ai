import { useOutletContext } from "react-router";
import { UnavailableState } from "../components/status";
import type { ShellOutletContext } from "../shell/AppShell";
import {
  BottomFactsRow,
  CompactSourcesPanel,
  LatencyEvidencePanel,
  QualificationStatusPanel,
  admissionStatus,
  getSourceQualificationView,
} from "./overview";
import {
  calendarWindowCount,
  engineeringFits,
  recordedFits,
  runCount,
} from "./snapshotFacts";
import styles from "./shared.module.css";
import layout from "./overview/overview.module.css";

export function OverviewPage() {
  const { snapshotResponse, capabilities } = useOutletContext<ShellOutletContext>();
  const snapshot = snapshotResponse.data;

  const windows = calendarWindowCount(snapshot);
  const fits = recordedFits(snapshot);
  const engFits = engineeringFits(snapshot);
  const runs = runCount(snapshot);
  const qualification = getSourceQualificationView(snapshot);
  const admission = admissionStatus(snapshot);
  const systemReason =
    snapshot.system.availability !== "AVAILABLE" ? snapshot.system.reasonCode : null;

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

      {qualification ? (
        <>
          <div className={layout.mainGrid}>
            <LatencyEvidencePanel qualification={qualification} />
            <QualificationStatusPanel
              qualification={qualification}
              systemReason={systemReason}
              admission={admission}
            />
          </div>
          <BottomFactsRow qualification={qualification} />
        </>
      ) : (
        <UnavailableState
          reasonCode={
            snapshot.sourceQualification.availability !== "AVAILABLE"
              ? snapshot.sourceQualification.reasonCode
              : "SOURCE_QUALIFICATION_UNAVAILABLE"
          }
        />
      )}

      <CompactSourcesPanel snapshot={snapshot} />

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
