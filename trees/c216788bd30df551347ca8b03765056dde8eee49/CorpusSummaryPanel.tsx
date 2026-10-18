import type { Snapshot } from "../../api";
import styles from "./corpus.module.css";

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
    <section className={styles.summaryStrip} aria-labelledby="corpus-summary-heading">
      <h2 id="corpus-summary-heading" className={styles.stripTitle}>
        Счётчики плана
      </h2>
      <div className={styles.samplingStrip} aria-label="Sampling plan counts">
        <div className={styles.samplingItem}>
          <span>revision</span>
          <strong>{c.revision}</strong>
        </div>
        <div className={styles.samplingItem}>
          <span>planned</span>
          <strong>{s.plannedWindows}</strong>
        </div>
        <div className={styles.samplingItem}>
          <span>development</span>
          <strong>{s.developmentWindows}</strong>
        </div>
        <div className={styles.samplingItem}>
          <span>reserved</span>
          <strong>{s.reservedWindows}</strong>
        </div>
        <div className={styles.samplingItem}>
          <span>admitted</span>
          <strong>{s.admittedWindows}</strong>
        </div>
        <div className={styles.samplingItem}>
          <span>usable</span>
          <strong>{c.usableDecisions}</strong>
        </div>
        <div className={styles.samplingItem}>
          <span>observed</span>
          <strong>{c.allScenariosObservedDecisions}</strong>
        </div>
      </div>
      <p className={styles.note}>
        <span className={styles.noteStrong}>collector не активирован</span>. 122 — не all-start; у окна 0
        scenarios 1440. Допуск закрыт.
      </p>
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
