import { useOutletContext } from "react-router";
import { UnavailableState, provenanceBadgeKind } from "../../components/status";
import type { ShellOutletContext } from "../../shell/AppShell";
import { PermissionMap } from "./PermissionMap";
import { ResearchDetails } from "./ResearchDetails";
import styles from "./research.module.css";

export function ResearchPage() {
  const { snapshotResponse } = useOutletContext<ShellOutletContext>();
  const pane = snapshotResponse.data.research;

  if (pane.availability !== "AVAILABLE") {
    return <UnavailableState reasonCode={pane.reasonCode ?? "RESEARCH_UNAVAILABLE"} />;
  }

  const provenance = provenanceBadgeKind(pane.provenance);

  return (
    <div className={styles.page}>
      <PermissionMap
        data={pane.data}
        qualification={pane.qualification}
        snapshot={snapshotResponse.data}
        provenance={provenance}
      />
      <ResearchDetails data={pane.data} provenance={provenance} />
    </div>
  );
}
