import { useState } from "react";
import { Lock } from "lucide-react";
import { Panel, ReasonDisclosure } from "../../components";
import { provenanceBadgeKind, QualificationBadge, UnavailableState } from "../../components/status";
import type { Capability, Snapshot } from "../../api";
import styles from "./overview.module.css";

type EvidenceAsideProps = {
  snapshot: Snapshot;
  capabilities: Capability[];
};

function CodeDisclosure({ label, codes }: { label: string; codes: string[] }) {
  const [open, setOpen] = useState(false);
  return (
    <div className={styles.details}>
      <button
        type="button"
        className={styles.disclosureButton}
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
      >
        {label}: {codes.length}
      </button>
      {open ? (
        <ul className={styles.codeList}>
          {codes.map((code) => (
            <li key={code}>
              <ReasonDisclosure label={label} value={code} />
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

export function EvidenceAside({ snapshot, capabilities }: EvidenceAsideProps) {
  return (
    <div className={styles.aside} data-cc-region="capability-cards">
      <CorpusCard snapshot={snapshot} />
      <ResearchCard snapshot={snapshot} />
      <SourceCard snapshot={snapshot} capabilities={capabilities} />
    </div>
  );
}

function CorpusCard({ snapshot }: { snapshot: Snapshot }) {
  const corpus = snapshot.corpus;
  if (corpus.availability !== "AVAILABLE") {
    return (
      <Panel title="Корпус">
        <UnavailableState reasonCode={corpus.reasonCode ?? "CORPUS_UNAVAILABLE"} />
      </Panel>
    );
  }
  const data = corpus.data;
  return (
    <Panel title="Корпус" density="compact">
      <div className={styles.badgeRow}>
        <QualificationBadge kind={provenanceBadgeKind(corpus.provenance)} />
        <QualificationBadge kind="ENGINEERING" />
      </div>
      <p className={styles.note}>
        revision {data.revision} · development {data.developmentWindows} · usable {data.usableDecisions} ·
        observed {data.allScenariosObservedDecisions}
      </p>
      <CodeDisclosure label="Допуск" codes={[data.researchPermission]} />
    </Panel>
  );
}

function ResearchCard({ snapshot }: { snapshot: Snapshot }) {
  const research = snapshot.research;
  if (research.availability !== "AVAILABLE") {
    return (
      <Panel title="Research">
        <UnavailableState reasonCode={research.reasonCode ?? "RESEARCH_UNAVAILABLE"} />
      </Panel>
    );
  }
  const data = research.data;
  return (
    <Panel title="Research" density="compact" className={styles.lockPanel}>
      <div className={styles.badgeRow}>
        <Lock size={16} strokeWidth={1.75} aria-hidden />
        <QualificationBadge kind="LOCKED" />
        <QualificationBadge kind="PROTECTED" />
      </div>
      <p className={styles.note}>
        Допуск к исследованию не выдан. {data.recordedResearchFits}/{data.fitBudget} · engineering{" "}
        {data.engineeringFits}. Holdout {data.holdout}.
      </p>
      <CodeDisclosure label="Квалификация" codes={[research.qualification]} />
      <CodeDisclosure label="Незакрытые review" codes={data.missingReviews} />
    </Panel>
  );
}

function SourceCard({
  snapshot,
  capabilities,
}: {
  snapshot: Snapshot;
  capabilities: Capability[];
}) {
  const qualification = snapshot.sourceQualification;
  const stamps = Object.values(snapshot.sources);
  const dates = [...new Set(stamps.map((stamp) => stamp.evidenceDate))];
  const unavailable = capabilities.filter((capability) => capability.availability !== "AVAILABLE");
  const availableCount = capabilities.length - unavailable.length;
  const reasonCodes = [...new Set(unavailable.map((capability) => capability.reasonCode ?? ""))].filter(
    (code) => code.length > 0,
  );

  return (
    <Panel title="Источник" density="compact">
      {qualification.availability !== "AVAILABLE" ? (
        <UnavailableState reasonCode={qualification.reasonCode ?? "SOURCE_QUALIFICATION_UNAVAILABLE"} />
      ) : (
        <>
          <p className={styles.verdict}>{qualification.data.recordedVerdict}</p>
          <p className={styles.note}>
            {qualification.data.sourceName}. Запросы probe {qualification.data.accepted}/
            {qualification.data.requests} — не индикатор live RPC. {stamps.length} источников
            {dates.length > 0 ? ` · ${dates[0]}` : ""}. Чтение: {availableCount} available,{" "}
            {unavailable.length} без контракта.
          </p>
          <CodeDisclosure label="Блокеры" codes={qualification.data.blockers} />
        </>
      )}
      <CodeDisclosure
        label="Коды"
        codes={[...reasonCodes, ...(snapshot.system.reasonCode ? [snapshot.system.reasonCode] : [])]}
      />
    </Panel>
  );
}
