import { useOutletContext } from "react-router";
import { Kpi } from "../components";
import type { ShellOutletContext } from "../shell/AppShell";
import {
  BottomFactsRow,
  D43Timeline,
  EvidenceAside,
  LatencyEvidencePanel,
  WindowSummary,
  d43Markers,
  engineeringRunGroups,
  getSourceQualificationView,
} from "./overview";
import { calendarWindowCount, engineeringFits, recordedFits, runCount } from "./snapshotFacts";
import layout from "./overview/overview.module.css";

export function OverviewPage() {
  const { snapshotResponse, capabilities } = useOutletContext<ShellOutletContext>();
  const snapshot = snapshotResponse.data;
  const windows = calendarWindowCount(snapshot);
  const fits = recordedFits(snapshot);
  const engFits = engineeringFits(snapshot);
  const runs = runCount(snapshot);
  const markers = d43Markers(snapshot);
  const qualification = getSourceQualificationView(snapshot);
  const models = snapshot.models.availability === "AVAILABLE" ? snapshot.models.data : null;

  return (
    <div className={layout.page}>
      <div className={layout.kpiRow} data-cc-region="overview-kpis">
        <Kpi
          label="Окна календаря"
          value={windows === null ? "—" : String(windows)}
          context={windows === null ? "календарь недоступен" : "записанные окна D43"}
        />
        <Kpi
          label="Recorded fits"
          value={fits ? `${fits.recorded}/${fits.budget}` : "—"}
          context={fits ? "только recorded budget" : "research недоступен"}
        />
        <Kpi
          label="Engineering fits"
          value={engFits === null ? "—" : String(engFits)}
          context="ENGINEERING, не admission"
        />
        <Kpi
          label="Инженерные runs"
          value={runs === null ? "—" : String(runs)}
          context="инженерный прогон, не рынок"
        />
      </div>

      <div className={layout.stage}>
        <D43Timeline
          markers={markers}
          unavailableReason={
            snapshot.calendar.availability === "AVAILABLE" ? null : snapshot.calendar.reasonCode
          }
        />
        <EvidenceAside snapshot={snapshot} capabilities={capabilities} />
      </div>
      <div className={layout.bottom}>
        <WindowSummary
          markers={markers}
          runGroups={engineeringRunGroups(snapshot)}
          runCount={runs}
          activeModelChosen={models ? models.activeModel !== null : null}
          marketBackboneSelected={models ? models.marketBackboneSelected : null}
        />
        <BottomFactsRow pools={qualification ? qualification.pools : null} />
        <LatencyEvidencePanel qualification={qualification} />
      </div>
    </div>
  );
}
