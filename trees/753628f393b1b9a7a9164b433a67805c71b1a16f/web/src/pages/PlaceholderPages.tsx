import { useOutletContext } from "react-router";
import { provenanceBadgeKind, QualificationBadge } from "../components/status";
import type { ShellOutletContext } from "../shell/AppShell";
import locked from "./replay/replay.module.css";

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
