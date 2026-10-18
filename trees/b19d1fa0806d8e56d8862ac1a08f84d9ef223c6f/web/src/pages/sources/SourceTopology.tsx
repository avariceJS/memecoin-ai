import { useId } from "react";
import type { Snapshot } from "../../api";
import { QualificationBadge, type ProvenanceBadgeKind } from "../../components/status";
import type { SourceQualificationView } from "../overview/selectors";
import { ProvenanceDrawer } from "./ProvenanceDrawer";
import styles from "./sources.module.css";
import {
  observationLine,
  policyLabel,
  qualificationLabel,
  verdictLabel,
} from "./viewModel";

type SourceTopologyProps = {
  snapshot: Snapshot;
  qualification: SourceQualificationView;
  sourceIds: readonly string[];
  provenance: ProvenanceBadgeKind;
};

export function SourceTopology({
  snapshot,
  qualification,
  sourceIds,
  provenance,
}: SourceTopologyProps) {
  const titleId = useId();
  const sourceTitle = useId();
  const qualificationTitle = useId();
  const projectionTitle = useId();
  const stampCount = Object.keys(snapshot.sources).length;

  return (
    <section className={styles.scheme} aria-labelledby={titleId}>
      <div className={styles.panelHead}>
        <h2 id={titleId} className={styles.panelTitle}>
          Источник → квалификация → проекция
        </h2>
        <QualificationBadge kind={provenance} />
      </div>
      <p className={styles.lede}>
        Записанная схема: источник, квалификация, проекция. Рёбра не показывают поток данных.
      </p>
      <div className={styles.plot}>
        <div className={styles.flow}>
          <article className={styles.node} aria-labelledby={sourceTitle}>
            <h3 id={sourceTitle} className={styles.kicker}>
              Источник
            </h3>
            <p className={styles.nodeTitle}>{qualification.sourceName}</p>
            {sourceIds.length > 0 ? (
              <ul className={styles.idChips} aria-label="Идентификаторы источника квалификации">
                {sourceIds.map((id) => (
                  <li key={id}>{id}</li>
                ))}
              </ul>
            ) : (
              <p className={styles.summary}>Идентификатор квалификации не опубликован.</p>
            )}
            <p className={styles.summary}>Commitment {qualification.commitment}</p>
            <p className={styles.summary}>{observationLine(snapshot, sourceIds)}</p>
          </article>
          <div className={styles.edge} data-edge="recorded" aria-hidden="true">
            <span className={styles.edgeLine} />
            <span className={styles.edgeLabel}>записано</span>
          </div>
          <article className={styles.node} aria-labelledby={qualificationTitle}>
            <h3 id={qualificationTitle} className={styles.kicker}>
              Квалификация
            </h3>
            <p className={styles.nodeTitle}>{qualificationLabel(qualification.qualification)}</p>
            <p className={styles.nodeToken}>{qualification.qualification}</p>
            <p className={styles.verdict}>{verdictLabel(qualification.recordedVerdict)}</p>
            <p className={styles.nodeToken}>{qualification.recordedVerdict}</p>
            <p className={styles.metric}>
              {qualification.accepted}/{qualification.requests}
            </p>
            <p className={styles.summary}>
              Запросы probe {qualification.accepted}/{qualification.requests} — зафиксированный прогон и
              не текущий RPC health.
            </p>
          </article>
          <div className={styles.edge} data-edge="recorded" aria-hidden="true">
            <span className={styles.edgeLine} />
            <span className={styles.edgeLabel}>записано</span>
          </div>
          <article className={styles.node} aria-labelledby={projectionTitle}>
            <h3 id={projectionTitle} className={styles.kicker}>
              Проекция
            </h3>
            <p className={styles.nodeTitle}>{policyLabel(snapshot.sourcePolicy)}</p>
            <p className={styles.nodeToken}>{snapshot.sourcePolicy}</p>
            <p className={styles.metric}>{stampCount}</p>
            <p className={styles.summary}>Зафиксированные штампы. Целостность PINNED_BYTES.</p>
            <ProvenanceDrawer snapshot={snapshot} />
          </article>
        </div>
      </div>
    </section>
  );
}
