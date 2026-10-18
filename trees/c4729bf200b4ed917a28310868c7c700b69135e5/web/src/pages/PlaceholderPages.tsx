import { useOutletContext } from "react-router";
import { provenanceBadgeKind, QualificationBadge, UnavailableState } from "../components/status";
import type { ShellOutletContext } from "../shell/AppShell";
import locked from "./replay/replay.module.css";
import {
  calendarWindowCount,
  engineeringFits,
  recordedFits,
  recordedVerdict,
  runCount,
} from "./snapshotFacts";
import styles from "./shared.module.css";

function FactSummary() {
  const { snapshotResponse } = useOutletContext<ShellOutletContext>();
  const snapshot = snapshotResponse.data;
  const windows = calendarWindowCount(snapshot);
  const fits = recordedFits(snapshot);
  const eng = engineeringFits(snapshot);
  const runs = runCount(snapshot);
  const verdict = recordedVerdict(snapshot);

  return (
    <p className={styles.factLine}>
      Окна: <span className={styles.factStrong}>{windows ?? "—"}</span>
      {" · "}
      recorded fits:{" "}
      <span className={styles.factStrong}>
        {fits ? `${fits.recorded}/${fits.budget}` : "—"}
      </span>
      {" · "}
      engineering fits: <span className={styles.factStrong}>{eng ?? "—"}</span>
      {" · "}
      runs: <span className={styles.factStrong}>{runs ?? "—"}</span>
      {" · "}
      verdict: <span className={styles.factStrong}>{verdict ?? "—"}</span>
    </p>
  );
}

function SimpleSection({ heading }: { heading: string }) {
  return (
    <section className={styles.panel}>
      <h2 className={styles.panelTitle}>{heading}</h2>
      <FactSummary />
    </section>
  );
}

export function CalendarPage() {
  return <SimpleSection heading="Календарь (recorded)" />;
}

export function ResearchPage() {
  return <SimpleSection heading="Исследование (recorded)" />;
}

export function ModelsPage() {
  return <SimpleSection heading="Модели (engineering)" />;
}

export function SourcesPage() {
  return <SimpleSection heading="Источники" />;
}

export function SystemPage() {
  const { snapshotResponse } = useOutletContext<ShellOutletContext>();
  const reason =
    snapshotResponse.data.system.availability !== "AVAILABLE"
      ? snapshotResponse.data.system.reasonCode
      : "NO_CURRENT_SERVICE_OR_HARDWARE_FEED";

  return (
    <>
      <SimpleSection heading="Система" />
      <UnavailableState reasonCode={reason} />
    </>
  );
}

export function TradingPage() {
  const { snapshotResponse } = useOutletContext<ShellOutletContext>();
  const trading = snapshotResponse.data.trading;
  const closed = trading.availability !== "AVAILABLE";
  const reason = closed ? trading.reasonCode : "NO_APPROVED_TRADING_CONTRACT";
  const provenance = closed ? null : provenanceBadgeKind(trading.provenance);

  return (
    <section
      className={locked.closedCard}
      data-tone={closed ? "unavailable" : "locked"}
      aria-labelledby="trading-closed-title"
    >
      <div className={locked.closedHead}>
        <h2 id="trading-closed-title" className={locked.closedTitle}>
          Торговля
        </h2>
        <QualificationBadge kind={closed ? "UNAVAILABLE" : "LOCKED"} />
        {provenance ? <QualificationBadge kind={provenance} /> : null}
      </div>
      <p className={locked.closedSummary}>
        Экран закрыт. Баланс, заявки и аварийные действия здесь не открываются.
      </p>
      <div
        className={locked.codeScroll}
        data-cc-scroll="trading-reason"
        tabIndex={0}
        aria-label="Код торгового контракта"
      >
        {reason}
      </div>
    </section>
  );
}
