import { QualificationBadge } from "../../components/status/QualificationBadge";
import type { Snapshot } from "../../api";
import styles from "./corpus.module.css";
import shared from "../shared.module.css";

type CorpusData = Extract<Snapshot["corpus"], { availability: "AVAILABLE" }>["data"];
type ResearchData = Extract<Snapshot["research"], { availability: "AVAILABLE" }>["data"];

type CorpusPaneAvailable = {
  availability: "AVAILABLE";
  qualification: string;
  data: CorpusData;
};

type ResearchPaneAvailable = {
  availability: "AVAILABLE";
  qualification: string;
  data: ResearchData;
};

type Props = {
  corpus: CorpusPaneAvailable;
  research: ResearchPaneAvailable;
};

export function CorpusSummaryPanel({ corpus, research }: Props) {
  const c = corpus.data;
  const s = research.data.sampling;

  return (
    <section className={styles.summaryPanel} aria-labelledby="corpus-summary-heading">
      <h2 id="corpus-summary-heading" className={shared.panelTitle}>
        Corpus и план D48 (замороженный design)
      </h2>
      <div className={styles.badgeRow}>
        <QualificationBadge kind="REAL EVIDENCE" />
        <QualificationBadge kind="ENGINEERING" />
        <QualificationBadge kind="LOCKED" />
        <span className={styles.note}>
          qualification {research.qualification} · admission {research.data.admission}
        </span>
      </div>
      <p className={styles.note}>
        Зафиксированный sampling-план;{" "}
        <span className={styles.noteStrong}>collector не активирован</span> (
        recordedCollectionAuthorized:{" "}
        {s.recordedCollectionAuthorized ? "true" : "false"}). Reserved-ячейки — защищённый
        design-holdout, не pending активного расписания D43.
      </p>

      <dl className={styles.metricGrid}>
        <div className={styles.metric}>
          <dt>revision</dt>
          <dd>{c.revision}</dd>
        </div>
        <div className={styles.metric}>
          <dt>development windows (corpus)</dt>
          <dd>{c.developmentWindows}</dd>
        </div>
        <div className={styles.metric}>
          <dt>usable decisions</dt>
          <dd>{c.usableDecisions}</dd>
        </div>
        <div className={styles.metric}>
          <dt>scenarios</dt>
          <dd>{c.scenarios}</dd>
        </div>
        <div className={styles.metric}>
          <dt>censored scenarios</dt>
          <dd>{c.censoredScenarios}</dd>
        </div>
        <div className={styles.metric}>
          <dt>all-scenarios observed</dt>
          <dd>{c.allScenariosObservedDecisions}</dd>
          <p className={styles.metricSub}>не all-start; у окна 0 scenarios 1440</p>
        </div>
        <div className={styles.metric}>
          <dt>greedy nonoverlap</dt>
          <dd>{c.greedyNonoverlapDecisions}</dd>
        </div>
        <div className={styles.metric}>
          <dt>nonoverlap independent</dt>
          <dd>{String(c.nonoverlapIsIndependent)}</dd>
        </div>
        <div className={styles.metric}>
          <dt>research permission</dt>
          <dd>{c.researchPermission}</dd>
        </div>
      </dl>

      <div className={styles.samplingStrip} aria-label="Sampling plan counts">
        <div className={styles.samplingItem}>
          <span>planned</span>
          <strong>{s.plannedWindows}</strong>
        </div>
        <div className={styles.samplingItem}>
          <span>development design</span>
          <strong>{s.developmentWindows}</strong>
        </div>
        <div className={styles.samplingItem}>
          <span>reserved / protected</span>
          <strong>{s.reservedWindows}</strong>
        </div>
        <div className={styles.samplingItem}>
          <span>admitted</span>
          <strong>{s.admittedWindows}</strong>
        </div>
      </div>
      <ul className={styles.offsetList} aria-label="UTC offsets">
        {s.utcOffsets.map((offset) => (
          <li key={offset} className={styles.offsetChip}>
            {offset}
          </li>
        ))}
      </ul>
    </section>
  );
}
