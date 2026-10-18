import { CircleOff } from "lucide-react";
import type { Snapshot, SnapshotResponse } from "../api";
import { Kpi, Panel, ReasonDisclosure } from "../components";
import { QualificationBadge } from "../components/status";
import { primaryEvidenceDate } from "../pages/snapshotFacts";
import styles from "./Header.module.css";

type HeaderProps = {
  title: string;
  snapshotResponse: SnapshotResponse | undefined;
  refreshing?: boolean;
  onRefresh: () => void;
};

function recordedStatus(snapshot: Snapshot): "recorded" | "demo" | "none" {
  const panes = [
    snapshot.calendar,
    snapshot.corpus,
    snapshot.research,
    snapshot.models,
    snapshot.sourceQualification,
    snapshot.system,
    snapshot.market,
    snapshot.predictions,
    snapshot.trading,
  ];
  let recorded = false;
  for (const pane of panes) {
    if (pane.availability !== "AVAILABLE") continue;
    if (pane.provenance === "DEMO") return "demo";
    if (pane.provenance === "REAL_EVIDENCE") recorded = true;
  }
  return recorded ? "recorded" : "none";
}

export function Header({ title, snapshotResponse, refreshing = false, onRefresh }: HeaderProps) {
  const snapshot = snapshotResponse?.data;
  const evidenceDate = snapshot ? primaryEvidenceDate(snapshot) : null;
  const status = snapshot ? recordedStatus(snapshot) : "none";
  const snapshotId = snapshotResponse?.snapshotId;

  return (
    <header className={styles.header} data-cc-region="header">
      <div className={styles.titleBlock}>
        <h1 className={styles.pageTitle}>{title}</h1>
        <Kpi
          compact
          label="Запись"
          value={evidenceDate ?? "—"}
          context={snapshot ? undefined : "Проекция не загружена"}
        />
      </div>
      <Panel density="inline" className={styles.statusPanel} label="Статус записи">
        {status === "recorded" ? (
          <span className={styles.recorded}>RECORDED EVIDENCE</span>
        ) : status === "demo" ? (
          <QualificationBadge kind="DEMO" />
        ) : (
          <span className={styles.quiet}>Нет записи</span>
        )}
        <p className={styles.coreNote}>
          <CircleOff className={styles.coreIcon} size={16} strokeWidth={1.75} aria-hidden />
          Состояние core недоступно
        </p>
        <button type="button" className={styles.refresh} onClick={onRefresh} disabled={refreshing}>
          Обновить
        </button>
        {snapshotId ? (
          <ReasonDisclosure
            label="snapshot"
            value={snapshotId}
            detail={snapshot ? { label: "baseline", value: snapshot.coreBaselineCommit } : null}
          />
        ) : null}
      </Panel>
    </header>
  );
}
