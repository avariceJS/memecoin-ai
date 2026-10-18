import { useOutletContext } from "react-router";
import { QualificationBadge, UnavailableState } from "../../components/status";
import type { ShellOutletContext } from "../../shell/AppShell";
import { ProtocolBudgetPanel } from "./ProtocolBudgetPanel";
import { ResearchAdmissionAside } from "./ResearchAdmissionAside";
import { ResearchKpiRow } from "./ResearchKpiRow";
import styles from "./research.module.css";

function provenanceBadge(
  provenance: string | null,
): "REAL EVIDENCE" | "ENGINEERING" | "SYNTHETIC INPUT" {
  if (provenance === "REAL_EVIDENCE") return "REAL EVIDENCE";
  if (provenance === "DEMO") return "SYNTHETIC INPUT";
  return "ENGINEERING";
}

export function ResearchPage() {
  const { snapshotResponse } = useOutletContext<ShellOutletContext>();
  const pane = snapshotResponse.data.research;

  if (pane.availability !== "AVAILABLE") {
    return (
      <UnavailableState reasonCode={pane.reasonCode ?? "RESEARCH_UNAVAILABLE"} />
    );
  }

  const data = pane.data;
  const provenanceLabel = provenanceBadge(pane.provenance);

  return (
    <>
      <header className={styles.pageHeader}>
        <div>
          <h2 className={styles.pageTitle}>Исследование (recorded)</h2>
          <p className={styles.policyNote}>
            Read-only консоль spec/gates/counters из published snapshot. Числовые checks не
            заменяют admission; UI не выдаёт research permission.
          </p>
        </div>
        <div className={styles.badgeRow}>
          <QualificationBadge kind={provenanceLabel} />
          <QualificationBadge kind="LOCKED" />
        </div>
      </header>

      <ResearchKpiRow data={data} />

      <div className={styles.mainGrid}>
        <ProtocolBudgetPanel data={data} qualification={pane.qualification} />
        <ResearchAdmissionAside data={data} />
      </div>
    </>
  );
}
