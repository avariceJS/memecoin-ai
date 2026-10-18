import { useOutletContext } from "react-router";
import { UnavailableState, provenanceBadgeKind } from "../../components/status";
import type { ShellOutletContext } from "../../shell/AppShell";
import { getSourceQualificationView } from "../overview/selectors";
import { PoolsCopyPanel } from "./PoolsCopyPanel";
import { ProbeChart } from "./ProbeChart";
import { SourceTopology } from "./SourceTopology";
import { SourcesStatusAside } from "./SourcesStatusAside";
import styles from "./sources.module.css";

export function SourcesPage() {
  const { snapshotResponse } = useOutletContext<ShellOutletContext>();
  const snapshot = snapshotResponse.data;
  const qualification = getSourceQualificationView(snapshot);
  const pane = snapshot.sourceQualification;

  if (!qualification || pane.availability !== "AVAILABLE") {
    return (
      <UnavailableState
        reasonCode={
          pane.availability !== "AVAILABLE"
            ? pane.reasonCode
            : "SOURCE_QUALIFICATION_UNAVAILABLE"
        }
      />
    );
  }

  const provenance = provenanceBadgeKind(pane.provenance);

  return (
    <div className={styles.page}>
      <div className={styles.mainColumn}>
        <SourceTopology
          snapshot={snapshot}
          qualification={qualification}
          sourceIds={pane.sourceIds}
          provenance={provenance}
        />
        <SourcesStatusAside qualification={qualification} provenance={provenance} />
      </div>
      <div className={styles.sideColumn}>
        <ProbeChart qualification={qualification} provenance={provenance} />
        <PoolsCopyPanel qualification={qualification} provenance={provenance} />
      </div>
    </div>
  );
}
