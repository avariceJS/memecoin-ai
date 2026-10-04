import { UnavailableState } from "../../components/status";
import type { Snapshot } from "../../api";
import { CorpusSummaryPanel } from "./CorpusSummaryPanel";
import { D48PlanGrid } from "./D48PlanGrid";
import styles from "./corpus.module.css";

type Props = {
  snapshot: Snapshot;
};

export function CalendarCorpusSections({ snapshot }: Props) {
  const corpus = snapshot.corpus;
  const research = snapshot.research;

  if (corpus.availability !== "AVAILABLE") {
    return (
      <UnavailableState reasonCode={corpus.reasonCode ?? "CORPUS_UNAVAILABLE"} />
    );
  }
  if (research.availability !== "AVAILABLE") {
    return (
      <UnavailableState reasonCode={research.reasonCode ?? "RESEARCH_UNAVAILABLE"} />
    );
  }

  return (
    <div className={styles.bottomRow} data-testid="calendar-corpus-sections">
      <D48PlanGrid sampling={research.data.sampling} />
      <CorpusSummaryPanel corpus={corpus} research={research} />
    </div>
  );
}
